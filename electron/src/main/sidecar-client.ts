import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import type { Handshake } from "../shared/contracts.js";

const PROTOCOL_VERSION = 1;
const MAX_MESSAGE_CHARS = 16 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 15_000;

interface RpcFailure {
  type: "response";
  protocolVersion: 1;
  id: string;
  ok: false;
  error: {
    code: string;
    message: string;
    retryable: boolean;
    data?: Record<string, unknown>;
  };
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: NodeJS.Timeout;
}

export interface SidecarClientOptions {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  requestTimeoutMs?: number;
}

export interface TransportEvent {
  event: string;
  data: Record<string, unknown>;
}

export class SidecarRpcError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly data?: Record<string, unknown>;

  constructor(error: RpcFailure["error"]) {
    super(error.message);
    this.name = "SidecarRpcError";
    this.code = error.code;
    this.retryable = error.retryable;
    if (error.data !== undefined) {
      this.data = error.data;
    }
  }
}

export class SidecarClient {
  private readonly options: SidecarClientOptions;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly eventListeners = new Set<(event: TransportEvent) => void>();
  private child: ChildProcessWithoutNullStreams | null = null;
  private decoder = new StringDecoder("utf8");
  private buffer = "";
  private nextId = 0;
  private handshakeValue: Handshake | null = null;
  private readyResolve: (() => void) | null = null;
  private readyReject: ((error: Error) => void) | null = null;

  constructor(options: SidecarClientOptions) {
    this.options = options;
  }

  get handshake(): Handshake | null {
    return this.handshakeValue;
  }

  async start(): Promise<Handshake> {
    if (this.child !== null && this.handshakeValue !== null) {
      return this.handshakeValue;
    }
    if (this.child !== null) {
      throw new Error("Sidecar 正在启动。");
    }

    const ready = new Promise<void>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
    const spawned = spawn(this.options.command, this.options.args, {
      cwd: this.options.cwd,
      env: this.options.env,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child = spawned;
    spawned.stdout.on("data", (chunk: Buffer) => this.consume(chunk));
    spawned.stderr.on("data", (chunk: Buffer) => {
      const diagnostic = chunk.toString("utf8").trim();
      if (diagnostic) {
        console.error(`[Novalist Sidecar] ${diagnostic}`);
      }
    });
    spawned.once("error", (error) => this.failProcess(error));
    spawned.once("exit", (code, signal) => {
      this.failProcess(
        new Error(`Sidecar 已退出（code=${String(code)}, signal=${String(signal)}）。`),
      );
    });

    const readyTimeout = setTimeout(() => {
      this.readyReject?.(new Error("等待 Sidecar 就绪超时。"));
    }, this.options.requestTimeoutMs ?? REQUEST_TIMEOUT_MS);
    try {
      await ready;
    } catch (error) {
      spawned.kill();
      const failure = error instanceof Error ? error : new Error("Sidecar 启动失败。");
      this.failProcess(failure);
      throw failure;
    } finally {
      clearTimeout(readyTimeout);
      this.readyResolve = null;
      this.readyReject = null;
    }

    try {
      const handshake = await this.request<Handshake>("system.handshake", {
        clientName: "Novalist Electron",
        clientVersion: "preview-1",
      });
      validateHandshake(handshake);
      this.handshakeValue = handshake;
      return handshake;
    } catch (error) {
      spawned.kill();
      const failure = error instanceof Error ? error : new Error("Sidecar 握手失败。");
      this.failProcess(failure);
      throw failure;
    }
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const child = this.child;
    if (child === null || child.stdin.destroyed) {
      return Promise.reject(new Error("Sidecar 未运行。"));
    }
    const id = `electron-${++this.nextId}`;
    const payload = JSON.stringify({
      type: "request",
      protocolVersion: PROTOCOL_VERSION,
      id,
      method,
      params,
    });
    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Sidecar 请求超时：${method}`));
      }, this.options.requestTimeoutMs ?? REQUEST_TIMEOUT_MS);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
        timeout,
      });
      child.stdin.write(`${payload}\n`, "utf8", (error) => {
        if (error) {
          this.rejectPending(id, error);
        }
      });
    });
  }

  onEvent(listener: (event: TransportEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (child === null) {
      return;
    }
    try {
      await this.request("system.shutdown");
    } catch {
      child.kill();
    }
    await new Promise<void>((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null) {
        resolve();
        return;
      }
      const timeout = setTimeout(() => {
        child.kill();
        resolve();
      }, 2_000);
      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  private consume(chunk: Buffer): void {
    this.buffer += this.decoder.write(chunk);
    if (this.buffer.length > MAX_MESSAGE_CHARS && !this.buffer.includes("\n")) {
      this.failProcess(new Error("Sidecar 输出消息超过限制。"));
      this.child?.kill();
      return;
    }
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trimEnd();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        this.handleLine(line);
      }
      newline = this.buffer.indexOf("\n");
    }
  }

  private handleLine(line: string): void {
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.failProcess(new Error("Sidecar 输出了无效 JSON。"));
      this.child?.kill();
      return;
    }
    if (!isRecord(message) || message.protocolVersion !== PROTOCOL_VERSION) {
      this.failProcess(new Error("Sidecar 输出协议版本不兼容。"));
      this.child?.kill();
      return;
    }
    if (message.type === "event") {
      if (typeof message.event !== "string" || !isRecord(message.data)) {
        this.failProcess(new Error("Sidecar 事件结构无效。"));
        this.child?.kill();
        return;
      }
      const event = { event: message.event, data: message.data };
      if (event.event === "sidecar.ready") {
        this.readyResolve?.();
      }
      for (const listener of this.eventListeners) {
        listener(event);
      }
      return;
    }
    if (message.type !== "response" || typeof message.id !== "string") {
      this.failProcess(new Error("Sidecar 响应结构无效。"));
      this.child?.kill();
      return;
    }
    const pending = this.pending.get(message.id);
    if (pending === undefined) {
      return;
    }
    this.pending.delete(message.id);
    clearTimeout(pending.timeout);
    if (message.ok === true && "result" in message) {
      pending.resolve(message.result);
      return;
    }
    if (message.ok === false && isRpcError(message.error)) {
      pending.reject(new SidecarRpcError(message.error));
      return;
    }
    pending.reject(new Error("Sidecar 响应缺少结果或错误。"));
  }

  private rejectPending(id: string, error: Error): void {
    const pending = this.pending.get(id);
    if (pending === undefined) {
      return;
    }
    this.pending.delete(id);
    clearTimeout(pending.timeout);
    pending.reject(error);
  }

  private failProcess(error: Error): void {
    this.readyReject?.(error);
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timeout);
      pending.reject(error);
      this.pending.delete(id);
    }
    this.handshakeValue = null;
    this.child = null;
  }
}

function validateHandshake(value: Handshake): void {
  if (
    !isRecord(value) ||
    value.protocolVersion !== PROTOCOL_VERSION ||
    !Array.isArray(value.supportedMethods) ||
    !value.supportedMethods.includes("project.open") ||
    !value.supportedMethods.includes("document.save")
  ) {
    throw new Error("Sidecar 握手能力不完整。");
  }
}

function isRpcError(value: unknown): value is RpcFailure["error"] {
  return (
    isRecord(value) &&
    typeof value.code === "string" &&
    typeof value.message === "string" &&
    typeof value.retryable === "boolean" &&
    (value.data === undefined || isRecord(value.data))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

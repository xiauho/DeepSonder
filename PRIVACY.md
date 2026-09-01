# Privacy and AI processing

## Local data

Novalist is a local desktop application. It does not operate a Novalist cloud
service, create user accounts, or include application telemetry. Story projects,
preferences, and AI task records are stored on the user's computer.

## Data sent through dsh

Novalist invokes the external `dsh` command only when the user starts an AI
action. Depending on that action, the submitted prompt may contain:

- the current chapter;
- outlines, the project writing-style guide, character profiles and world-building notes;
- chapter summaries and story state;
- the instruction needed to continue, review or summarize the story.

The configured DeepSeek Harness installation and its selected model provider
determine where that data is processed, logged and retained. Users should read
the terms and privacy documentation of those services before using AI features.

For prompts that are too large for a safe command-line invocation, Novalist may
write the complete prompt to a randomly named UTF-8 task file in its private
temporary Harness workspace. The configured local Harness process reads that
file before contacting its model provider. Novalist attempts to delete each
task file after success, failure, timeout, or cancellation, and removes the
private workspace when the AI client is released. The file is never written
inside the user's novel project.

After each AI invocation, Novalist may display an in-memory context report in
the local interface. This report contains task and chapter identifiers,
character counts, allocation states, transport mode, invocation outcome, and
temporary-file cleanup status. It does not retain prompt text, chapter prose,
canon text, credentials, or absolute file paths. Copying the diagnostic report
copies only these redacted metrics; Novalist does not upload the report itself.

Do not submit personal data, confidential information, or copyrighted material
that you are not authorized to process. AI output may be inaccurate or unsuitable
and must be reviewed before use or publication.

## Credentials

Novalist does not request or store a DeepSeek API key. Credentials are managed
outside Novalist by the user's Harness installation. Never commit `.env`, API
keys, tokens, personal `config.json` files, or private writing projects.

## Update checks

Manual update checks, and automatic checks when explicitly enabled, request the
public GitHub Releases list for `xiauho/novalist`. These requests do not include
story projects, prompts, AI credentials, or the contents of `config.json`.
GitHub receives ordinary network metadata such as the user's IP address and a
Novalist version User-Agent. Automatic checks run at most once every 24 hours.

The current updater only displays release information and can open the official
Novalist GitHub release page. It does not download or execute update files.

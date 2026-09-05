# Privacy and AI processing

## Local data

Novalist is a local desktop application. It does not operate a Novalist cloud
service, create user accounts, or include application telemetry. Story projects,
preferences, and AI task records are stored on the user's computer.

When an older project format is upgraded, Novalist creates a local pre-migration
backup under that project's `.novalist/backups/migration-*` directory. The
backup may contain story state, summaries, canon metadata, and other affected
project files, so it must be treated as private writing data. Migration backups
are not uploaded and are retained until the user removes them. A temporary
local migration journal contains file paths and schema numbers, but not copied
prose; it is removed after a successful migration or recovery.

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

For every AI task, Novalist writes the complete business prompt to a randomly
named UTF-8 task file in its private temporary Harness workspace. The command
line carries only a short loader instruction and never the chapter or canon.
The configured local Harness process reads that file before contacting its model
provider. Novalist attempts to delete each task file after success, failure,
timeout, or cancellation, and removes the private workspace when the AI client
is released. The file is never written inside the user's novel project. The
response must echo random challenges from the file head, payload midpoint and
file tail before Novalist accepts the business result.

After each AI invocation, Novalist may display an in-memory context report in
the local interface. This report contains task and chapter identifiers,
character counts, conservative estimated-token counts, allocation states,
transport mode, invocation outcome, and temporary-file cleanup status. It does
not retain prompt text, chapter prose, canon text, credentials, or absolute file
paths. Copying the diagnostic report copies only these redacted metrics;
Novalist does not upload the report itself.

If a prose-generation response omits the requested protocol markers and is
accepted through the plain-text compatibility fallback, Novalist displays an
in-memory count for that invocation and for the current application session.
The counter does not store or reproduce the returned prose.

The chapter fact-ledger memory workflow may store validated
chunk summaries and structured facts in the operating system's application
cache directory. This disposable cache is keyed by content hashes so unchanged
chunks do not need to be sent again. It does not store the original raw chapter
chunks, but its derived summaries and facts may still reveal story content and
should be treated as private local data. Deleting the Novalist application
cache removes these entries; they can be rebuilt from the project when needed.

The ledger-based memory pipeline may also cache derived chapter summaries,
evidence-bound state patches, and conflict descriptions. These entries are
keyed by chapter, state, and selected-context hashes, do not contain the raw
chapter text, and are invalidated when their source state changes. They can
still reveal plot and character information and should be treated as private
local data.

Explicitly adopting a chapter-memory proposal also saves a portable record in
the project's `memory/accepted_chapter_memory.json`. This is retained project
data, not disposable application cache: it contains derived digests, evidence
facts with paragraph anchors, source hashes, and story-state snapshots and
dependencies. It may reveal plot details even after the source chapter is
deleted. Deleting a chapter does not erase this record; source validation keeps
deleted or modified chapters out of remote-history retrieval. Treat this file
as private writing data when sharing or backing up the project.

Remote-history selection here means earlier chapters in the local project,
not a network search. Matching and source validation make no model requests.
When an AI task is submitted, the selected historical facts are included in
its ordinary file-bridged prompt. Diagnostic reports include source chapter
IDs, version hashes, fact IDs and generic match reasons, but not matched prose
or search queries.

Context selection ranks canon locally using explicit
references such as character names, document titles, Markdown headings,
aliases, and tags. Selection does not make an additional model request and
does not send data anywhere by itself. Reports may include aggregate candidate,
matched, included, and excluded counts plus generic selection reasons, but not
canon filenames or the matched text.

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

Verified Windows x64 portable packages may be installed by the standalone
Novalist updater after explicit user confirmation. Unsupported or older
installations retain the manual download-and-extract path. See the packaging
documentation for the updater's verification and rollback boundary.

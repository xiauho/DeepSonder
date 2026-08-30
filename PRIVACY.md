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

Do not submit personal data, confidential information, or copyrighted material
that you are not authorized to process. AI output may be inaccurate or unsuitable
and must be reviewed before use or publication.

## Credentials

Novalist does not request or store a DeepSeek API key. Credentials are managed
outside Novalist by the user's Harness installation. Never commit `.env`, API
keys, tokens, personal `config.json` files, or private writing projects.

# Music Licensing Reference

The skill may download intro/outro background music only from sources with explicit license metadata.

## Default Safe Policy

Automatically mix into the final podcast only when the track is clearly:

- CC0
- Public Domain
- Public Domain Mark

Do not auto-mix tracks with missing, unclear, restrictive, or commercial-platform-only licenses.

## Attribution Music

Creative Commons attribution tracks can be useful, but they should not be mixed automatically unless the user opts in with `--allow-cc-attribution-music`. When attribution music is used, write the title, creator, source URL, and license URL into `music_license_report.md` and `show_notes.md`.

Only recognized CC BY 1.0/2.0/2.5/3.0/4.0 is accepted by that switch. It does not allow CC BY-NC, CC BY-ND, CC BY-SA or unknown/custom licenses. Metadata URLs must have the actual `creativecommons.org` host and recognized license paths. Metadata declarations are not proof of copyright ownership; record sources and listen to the selected files.

Candidate downloads are capped at 80 MB each and bounded to five candidates. HTML/JSON responses are rejected and actual audio is decoded before inclusion. A missing safe selection produces a music report and an unscored, music-free export.

Provided music requires a license URL or user permission description, saved as a user declaration rather than independent verification. Stingers are shortened, faded and normalized; credits identify these modifications. The MVP does not verify instrumentalness through AI or create a persistent music bed.

## Prohibited Sources

Do not scrape or download audio from Spotify, Apple Music, Bilibili, YouTube, NetEase Cloud Music, QQ Music, or other commercial streaming platforms.

## Preferred Sources

- Internet Archive Advanced Search/Metadata API for no-key discovery.
- Jamendo API only when a `JAMENDO_CLIENT_ID` is available and the API reports download permission and license metadata.

Primary references: https://developer.jamendo.com/v3.0/tracks and https://creativecommons.org/licenses/by/4.0/

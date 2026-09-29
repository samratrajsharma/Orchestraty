# Releasing the scanner to participants

Status: throwaway (Phase 0). Not part of the product.

No build of the scanner goes to anyone until every step below is done. Sending it is a private disclosure, so it needs the owner's approval each time.

1. **Tests pass.** Run them from `spikes/`:

   ```
   python -m pytest
   ```

   Every spike job in CI must also pass.
2. **Build the single file.** From `spikes/scan/`:

   ```
   python scan_bundle.py
   ```

   This writes `out/orch-scan.py`, which is gitignored, and prints the file's SHA-256. Record that hash with the release. It identifies exactly what each participant ran.
3. **Same build everywhere.** The owner builds the file from the same commit on Windows and on WSL2. Every build must print the recorded SHA-256. A different hash means the build is not reproducible, and nothing is sent until the reason is found. macOS is covered by a green manual run of the macOS CI job on that same commit.
4. **Owner test runs.** The owner runs the built file on Windows and on WSL2. Before saving, they read the full preview on screen and check it against `PRIVACY.md`:
   - no path, repository name, email, helper name or prompt text appears;
   - the counts look plausible;
   - nothing else was written.
5. **Approval.** The owner reads the built file and the test-run outputs, then approves that exact hash.
6. **Sending.**
   - **Participants on Windows and Linux** can get the file once steps 1–5 have passed.
   - **Participants on macOS** get it only after one trusted Mac user has run it while the owner reads the preview with them (for example, on a screen share), and nothing unexpected appeared.

   Email clients often block `.py` attachments (Outlook for Windows and Outlook on the web do), so the file is never attached. Each participant gets:
   - an invitation-only share link to the built file, shared with them by name, never a public link, package index or public gist;
   - `PRIVACY.md`;
   - the file's SHA-256 in the message itself, so they can check the file before running it (`PRIVACY.md` says how).
7. **Receiving.** Participants email their result file (JSON) back themselves. On receipt, the owner:
   - saves it to `%USERPROFILE%\orch-phase0\scan-results`, outside this repository and outside any synced folder, under a participant code, never under the person's name;
   - adds the code to the separate mapping file (code, name, email) kept in the same folder;
   - deletes the email, including from Deleted Items or Trash.

   Result files are never committed. The `.gitignore` patterns `orch-scan-*.json`, `spikes/**/out/` and `spikes/**/data/` guard against mistakes. Analysis uses the coded files only, never the emails.
8. **Keeping and deleting.**
   - Result files and the mapping file are deleted when the Phase 0 report is final. Only totals across all participants are kept, in the report.
   - On request, a person's result file and their line in the mapping file are deleted within 7 days, and the owner confirms by email. Totals already in a report are not recalculated.

A change to the scanner after approval means a new build, a new hash and a new approval.

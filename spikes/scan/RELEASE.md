# Releasing the scanner to participants

Status: throwaway (Phase 0). Not part of the product.

No build of the scanner goes to anyone until every step below is done. Sending it is a private disclosure, so it needs the owner's approval each time.

1. **Tests pass.** Run them from `spikes/`:

   ```
   python -m pytest
   ```

   CI must also pass on Linux (Python 3.9 to 3.12) and Windows (3.12).
2. **Build the single file.** From `spikes/scan/`:

   ```
   python scan_bundle.py
   ```

   This writes `out/orch-scan.py`, which is gitignored, and prints the file's SHA-256. Record that hash with the release. It identifies exactly what each participant ran.
3. **Owner test runs.** The owner runs the built file on each environment: Windows, WSL2 and macOS. Before saving, they read the full preview on screen and check it against `PRIVACY.md`:
   - no path, repository name, email, helper name or prompt text appears;
   - the counts look plausible;
   - nothing else was written.
4. **Review and approval.** The owner and the reviewer read the built file and the test-run outputs, then approve that exact hash.
5. **Sending.** The built file and `PRIVACY.md` go privately to each participant, by direct email or an invitation-only share. Never through a public link, package index or public gist.
6. **Receiving.** Participants email their result file back themselves. Results are stored on the owner's machine under `%LOCALAPPDATA%\orch-phase0\scan-results`, outside this repository and outside any synced folder. They are never committed. The `.gitignore` patterns `orch-scan-*.json`, `spikes/**/out/` and `spikes/**/data/` guard against mistakes.

A change to the scanner after approval means a new build, a new hash and a new approval.

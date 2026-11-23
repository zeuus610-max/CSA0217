# Pocket — FamPay budget tracker

Standalone local website with a read-only Gmail OAuth connector. Gmail authorization is user-consented; this project does not receive or store your Google password.

## Run locally

1. In Google Cloud Console, create a project, enable the **Gmail API**, configure the OAuth consent screen, and add your Gmail account as a test user while the app is in Testing.
2. Create an OAuth client ID of type **Web application**. Add this authorized JavaScript origin:

   `http://127.0.0.1:8765`

   Add this authorized redirect URI:

   `http://127.0.0.1:8765/api/auth/callback`

3. From this directory, copy `.env.example` to `.env` and put the client ID and secret in `.env` using a local editor. Keep the client secret private; `.env` is git-ignored, and do not paste the secret into chat.

   ```sh
   cp .env.example .env
   # Edit .env and replace the two placeholder values.
   ./run.sh
   ```

4. Open `http://127.0.0.1:8765`, click **Connect Gmail**, review Google's consent screen, and approve the requested read-only Gmail permission.

## Data and limitations

- The connector requests `https://www.googleapis.com/auth/gmail.readonly`. Google classifies this as a restricted Gmail scope. A public production app needs Google's applicable verification; server handling of restricted Gmail data can also require a security assessment. For personal development, use a testing OAuth consent screen and your own allowlisted Google account.
- OAuth tokens are held only in the local server's memory. They are not written to disk or logs. Restarting the server clears them and requires reconnecting Gmail.
- When the dashboard is open, it checks for FamPay emails on load and every 60 seconds. This is not a background Gmail push notification, and sync stops when the local app is closed.
- It searches the last 90 days and heuristically extracts amounts, merchant text, and categories. Review imported totals for accuracy; FamPay email formats can vary. The dashboard shows only parsed transactions.
- The app binds to `127.0.0.1` for local use. Do not expose this development server to the public internet.

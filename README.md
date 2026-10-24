# TVS Parts Inventory and AI Forecast Hub

This app runs locally with Python, Flask, SQLite, and browser-side HTML, CSS, and JavaScript.

## Run locally on Windows

1. Install Python 3.10 or newer.
2. Extract this ZIP, open the extracted `TVSInventoryUpgrade` folder, and double-click `run_local.bat`. It creates a private `.venv` folder and installs the pinned Flask dependency the first time it runs.
3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000). On first launch, create an administrator username and password at the setup screen. Later visits open the login screen.

If Windows cannot find Python, install Python 3.10 or newer and enable the Python launcher or **Add Python to PATH**, then double-click `run_local.bat` again. A working internet connection is needed the first time so Flask can be installed.

## Run from VS Code on Windows

1. In VS Code, use **File → Open Folder** and select the extracted `TVSInventoryUpgrade` folder (open the folder, not just `app.py`).
2. Open **Terminal → Run Task…**, select **Run TVS Inventory Website**, and wait for the server message in the integrated terminal. The task runs `run_local.bat`, which prepares the local environment and starts Flask.
3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser. Keep the VS Code terminal running while you use the site; press **Ctrl+C** there to stop it.

The workspace recommends Microsoft's Python extension and points it to the project's `.venv` interpreter. If VS Code asks you to choose an interpreter, select the `.venv` environment after the first run creates it. You can also start the app directly from a terminal or double-click `run_local.bat`.

The setup screen creates the first administrator; there is no shared default password. Sign in with that username and password. Use **Admin Accounts** to add more administrators. Passwords are stored as salted hashes in SQLite.

The server binds to `127.0.0.1` and runs with debug mode off by default. Set `PORT` to choose another port. `TVS_DB_PATH` can point to a different SQLite database. The app applies additive schema migrations at startup and does not seed demo products or sales.

## Local AI forecasts and copilot

Install [Ollama](https://ollama.com/download) and download the free local model from PowerShell:

```powershell
ollama pull qwen2.5:1.5b
```

Start Ollama and then start the Flask app. The published 1.5B model is about 986 MB. Forecasts and copilot questions are sent only to the Ollama service on `localhost`; the app does not call a paid model API. The model can be changed with `OLLAMA_MODEL`, and its local URL with `OLLAMA_BASE_URL` (localhost addresses only).

Open **AI Demand Forecast**, select a product and choose a horizon from 1 to 12 months. The API is `POST /api/forecasts/monthly`; `GET /api/forecasts/monthly/<product_id>?horizon=6` is also available. The local LLM generates the final forecast values and planning ranges using linked sales history, product stock, reorder level, and any selected comparable product or planning estimate. The app validates the response and derives a reorder suggestion from it.

When a product has no linked sales, select a same-category comparable product with at least three months of history or enter a monthly planning estimate. Predictions need review, especially when history is sparse. AI forecast accuracy is not backtested yet. The copilot answers inventory questions from current stock and the last 90 days of product-linked sales; it does not change inventory or place orders.

## Dashboard and inventory

The dashboard compares recorded sales over 7, 30, 90, or 365 days with the previous equal-length period. Analytics includes interactive product-share and monthly trend charts. The inventory manager supports product filters, quick sale logging, stock receipts with invoice references, stock movement history, and dashboard CSV export. Billing, workshop cards, and customer history are retained.

The app does not remap sales identifiers that do not match inventory IDs or silently alter existing stock. Review data coverage and negative stock balances before relying on forecasts.

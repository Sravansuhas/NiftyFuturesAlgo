# NiftyFuturesAlgo — Morning Trading Baby Steps Guide

**Goal**: Safely start the live/paper trading system every morning with minimal friction and maximum safety.

This guide is intentionally written in the simplest possible language.

---

## Prerequisites (Do Once)

- You have a working `.env` file with valid Kite credentials.
- You have the latest code (`git pull` recommended).
- You know the project folder location.

---

## Every Morning — Step by Step

### 1. Open Terminal / Command Prompt

On Windows (PowerShell or CMD):
```powershell
cd C:\Projects\NiftyFuturesAlgo
```

### 2. Start the Entire System (One Command)

```powershell
PYTHONPATH=. python run.py
```

**What happens**:
- The trading engine starts in the background.
- The web dashboard also starts.
- You will see relatively calm startup messages (we removed the giant noisy banners).

Wait until you see a line like:
```
[RUNNER] Ready. Dashboard: http://localhost:8050
```

### 3. Open the Dashboard in Browser

Go to this address:

**http://localhost:8050**

Two important pages:
- **Main view** (http://localhost:8050) → Live risk, P&L, position, market status.
- **Algo Lab** (http://localhost:8050/backtest) → For backtesting (usually not needed during live hours).

### 4. Do These 4 Quick Safety Checks (Takes 20 seconds)

In the main dashboard:

1. **Market Status Rail** (top of page)
   - Should show **OPEN** (green or amber).
   - Should be inside the safe trading window.

2. **RiskGatekeeper Box**
   - Daily Loss should be low / zero at the start.
   - Position should be 0 before you take any trades.

3. **Reconciliation Status**
   - Should show that broker positions match our internal state.

4. **Token Status**
   - Should say token is valid.

### 5. During the Trading Day

- Keep the terminal window open (or use tmux / split screen).
- The terminal is now intentionally calm — most noise is hidden.
- Use the **dashboard** for detailed live information.
- If you ever start a long backtest from the Algo Lab and want to stop it → Click the red **"STOP / CANCEL RUNNING JOB"** button.

### 6. End of Day Shutdown (Very Important)

In the terminal where `run.py` is running, simply press:

**Ctrl + C**

The system will:
- Save final state
- Stop the trading engine cleanly
- Stop the dashboard

Wait until it says shutdown is complete.

---

## Pro Tips for Smooth Morning Starts

- Start the system **before 9:15 IST** so indicators can warm up.
- If you get token errors → run `python generate_token.py` first.
- The new "Statistical Power Warning" system will now block you from exporting garbage backtest reports (this is intentional and good).
- If something feels wrong, first look at the **Risk** section and **Reconciliation** in the dashboard.

---

**You are now ready for morning trading.**

The system is designed to be calm in the terminal and rich in the dashboard. Trust the dashboard more than the scrolling logs.

Good luck and trade safely.

"""
forecast.py - Next-24-hour demand forecast for GridSense.

Two models, compared honestly on the last 20% of the data:
  - baseline: average of the same hour over the previous 7 days
  - GRU (PyTorch): reads the last 48 hours + time-of-day/week and predicts the next 24

The better one (lower test error) is served. If PyTorch isn't installed or training
fails, the baseline is served, so /forecast never breaks.
"""

import threading

import numpy as np
import pandas as pd

LOOKBACK = 48
HORIZON = 24
_lock = threading.Lock()
_cache = None


def _time_features(ts: pd.DatetimeIndex) -> np.ndarray:
    h, d = np.asarray(ts.hour), np.asarray(ts.dayofweek)
    return np.stack([np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24),
                     np.sin(2 * np.pi * d / 7), np.cos(2 * np.pi * d / 7)], axis=1)


def baseline_next(series: np.ndarray, t: int) -> np.ndarray:
    """Forecast hours t..t+23 using only data before t: mean of same hour, last 7 days."""
    out = np.zeros(HORIZON)
    for h in range(HORIZON):
        idx = [t - 24 * k + h for k in range(1, 8) if t - 24 * k + h >= 0 and t - 24 * k + h < t]
        out[h] = series[idx].mean() if idx else series[:t].mean()
    return out


def _train_gru(series, feats, split):
    """Train the GRU on origins before `split`. Returns a predict(t) function."""
    import torch
    from torch import nn

    torch.manual_seed(0)
    mu, sd = series[:split].mean(), series[:split].std() + 1e-6
    x = np.concatenate([((series - mu) / sd)[:, None], feats], axis=1).astype(np.float32)

    def sample(t):
        fut = feats[t:t + HORIZON, :2].reshape(-1)               # future hour-of-day features
        return x[t - LOOKBACK:t], fut.astype(np.float32), ((series[t:t + HORIZON] - mu) / sd).astype(np.float32)

    origins = list(range(LOOKBACK, split - HORIZON + 1))
    X = torch.tensor(np.stack([sample(t)[0] for t in origins]))
    F = torch.tensor(np.stack([sample(t)[1] for t in origins]))
    Y = torch.tensor(np.stack([sample(t)[2] for t in origins]))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(x.shape[1], 32, batch_first=True)
            self.head = nn.Sequential(nn.Linear(32 + HORIZON * 2, 64), nn.ReLU(), nn.Linear(64, HORIZON))

        def forward(self, seq, fut):
            _, h = self.gru(seq)
            return self.head(torch.cat([h[-1], fut], dim=1))

    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=5e-3)
    for _ in range(40):
        perm = torch.randperm(len(X))
        for i in range(0, len(X), 64):
            b = perm[i:i + 64]
            opt.zero_grad()
            loss = nn.functional.mse_loss(net(X[b], F[b]), Y[b])
            loss.backward()
            opt.step()
    net.eval()

    def predict(t):
        seq = torch.tensor(x[t - LOOKBACK:t][None])
        if t + HORIZON <= len(feats):
            fut_feats = feats[t:t + HORIZON, :2]
        else:  # beyond the data: build future time features from the clock
            fut_feats = _future_feats(t, len(feats))
        fut = torch.tensor(fut_feats.reshape(1, -1).astype(np.float32))
        with torch.no_grad():
            return net(seq, fut).numpy()[0] * sd + mu

    return predict


_future_ts = None


def _future_feats(t, n):
    return _time_features(_future_ts)[:, :2]


def compute_forecast(df: pd.DataFrame) -> dict:
    global _future_ts
    df = df.sort_values("timestamp").reset_index(drop=True)
    series = df["demand_kwh"].to_numpy(dtype=float)
    ts = pd.DatetimeIndex(df["timestamp"])
    n = len(series)
    split = int(n * 0.8)
    feats = _time_features(ts)
    future_ts = pd.date_range(ts[-1] + pd.Timedelta(hours=1), periods=HORIZON, freq="h")
    _future_ts = future_ts

    # Evaluate on the held-out last 20%, one forecast per day
    test_origins = [t for t in range(split, n - HORIZON + 1, 24)]
    base_mae = float(np.mean([np.abs(baseline_next(series, t) - series[t:t + HORIZON]).mean()
                              for t in test_origins]))

    gru_mae, method, pred = None, "baseline", baseline_next(series, n)
    try:
        predict = _train_gru(series, feats, split)
        gru_mae = float(np.mean([np.abs(predict(t) - series[t:t + HORIZON]).mean() for t in test_origins]))
        if gru_mae < base_mae:
            method, pred = "gru", np.clip(predict(n), 0, None)
    except Exception as exc:  # torch missing or training failed -> baseline only
        print(f"[forecast] GRU unavailable, using baseline: {exc}")

    pred = np.clip(pred, 0, None)
    peak = int(np.argmax(pred))
    return {
        "method": method,
        "metrics": {
            "baseline_mae_kwh": round(base_mae, 3),
            "gru_mae_kwh": None if gru_mae is None else round(gru_mae, 3),
            "improvement_pct": None if gru_mae is None else round((base_mae - gru_mae) / base_mae * 100, 1),
            "test_days": len(test_origins),
        },
        "hours": [{"timestamp": str(t), "kwh": round(float(v), 3)} for t, v in zip(future_ts, pred)],
        "next_24h_kwh": round(float(pred.sum()), 2),
        "peak_hour": {"timestamp": str(future_ts[peak]), "kwh": round(float(pred[peak]), 2)},
    }


def get_forecast(df: pd.DataFrame) -> dict:
    """Compute once, then reuse (training takes a few seconds)."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = compute_forecast(df)
        return _cache


if __name__ == "__main__":
    from data import load_or_create

    r = get_forecast(load_or_create())
    print(r["method"], r["metrics"])
    print("next 24h kWh:", r["next_24h_kwh"], "peak:", r["peak_hour"])

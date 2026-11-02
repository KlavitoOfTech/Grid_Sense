# GridSense ⚡
### Energy Intelligence Platform

**Understand Your Energy. Control Your Costs.**

## 💡 The Problem

Households and small businesses in Nigeria face unreliable electricity, frequent power outages, rising energy costs, and increasing dependence on generators. Many lack the tools to monitor consumption, understand expenses, and plan their energy usage effectively.

## 🚀 Our Solution

GridSense is an energy intelligence platform that helps users monitor electricity consumption, analyze energy expenses, identify unusual usage patterns, and make smarter energy decisions through data analytics and forecasting.

## ✨ Key Features

- **Energy Dashboard:** View energy consumption, costs, and grid versus generator usage.
- **Consumption Analysis:** Understand usage patterns and peak demand periods.
- **Cost Comparison:** Compare grid and generator energy expenses.
- **Anomaly Detection:** Identify unusual consumption patterns.
- **Energy Forecasting:** Estimate future energy demand and costs.
- **Savings Recommendations:** Get practical suggestions for reducing energy expenses.
- **Data Extraction:** Extract useful information from uploaded energy-related images.

## 🛠️ Technology Stack

| Component | Technology |
|---|---|
| Frontend | React.js |
| Backend | Python, FastAPI |
| Data Processing | Pandas, NumPy |
| Visualization | Recharts |
| Forecasting | Python |
| Database | Supabase / PostgreSQL (planned) |

## ⚙️ Getting Started

**1. Clone the repository**

```bash
git clone YOUR_REPOSITORY_URL
cd GridSense
```

**2. Set up the backend**

```bash
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file and configure the required environment variables.

Start the backend:

```bash
python -m uvicorn main:app --reload
```

**3. Set up the frontend**

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

## 🔮 Future Improvements

- User authentication and personalized dashboards.
- Smart meter and IoT integration.
- Advanced consumption forecasting.
- Renewable energy cost comparisons.
- Automated energy reports and notifications.

## 🎯 Project Impact

GridSense aims to help households and businesses make informed energy decisions, reduce unnecessary expenses, and better manage their electricity consumption.

---

**GridSense — Understand Your Energy. Control Your Costs. Make Smarter Decisions.** ⚡

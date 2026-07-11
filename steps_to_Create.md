# How to Build AQROS — A Beginner's Step-by-Step Guide

> Hi! This guide explains **how to actually build this project**, step by step, in plain language. It assumes you are a beginner. I'll tell you exactly what tools to use, which ones are **free** (or very cheap), and what to do at each step.
>
> **Read this honestly first:** AQROS (the full vision in the `docs/` folder) is a *massive* project — a real hedge fund would use a team of 20+ engineers and years to build it. **You are not going to build all of it, and you should not try to.** Instead, you will build a small but *real and impressive* slice of it. That slice is more than enough for a placement portfolio and a startup demo. This guide shows you how to build that slice, and how to grow it later.
>
> **The golden rule for beginners:** build one small thing that *actually works* end-to-end, before adding anything else. A tiny working system beats a huge broken one — every time.

---

## Part 0: Understand What You're Building (5-minute read)

Think of AQROS like a **robot investment analyst** with these parts:

1. **Eyes** — it reads market data (stock prices, news).
2. **Memory** — it stores that data cleanly so it never "cheats" by looking at the future.
3. **Brain** — it uses machine learning + AI agents to make predictions and decisions.
4. **Hands** — it places trades (first fake/paper trades, real money only much later).
5. **Conscience** — a risk checker that can say "no" to any trade, and it explains every decision.

We build these in **4 stages**, and we never rush to real money:

```
STAGE 1 (MVP)      → Research only. Read data, predict, test on history. NO trading.
STAGE 2 (V1)       → Paper trading. Fake money, live prices. Then tiny real money.
STAGE 3 (V2)       → The AI "brain" of agents that debate. Smart autonomy.
STAGE 4 (Future)   → More markets (crypto, forex), bigger scale.
```

**You will focus on Stage 1 first.** That alone is a great placement/startup project.

---

## Part 1: Set Up Your Computer (Day 1)

Before writing anything, install these free tools. Just install them one by one.

| Tool | What it is | Cost | How to get it |
|---|---|---|---|
| **Python** | The main coding language | Free | python.org (get version 3.11+) |
| **Git** | Saves versions of your code | Free | git-scm.com |
| **VS Code** | The editor where you write code | Free | code.visualstudio.com |
| **Docker Desktop** | Runs databases & services in "boxes" | Free | docker.com |
| **GitHub account** | Stores your code online, shows employers | Free | github.com |

**AI coding helpers** (pick ONE to start — they write code *with* you):

| Tool | Why it's good for you | Cost |
|---|---|---|
| **Google Antigravity** | Google's new AI coding platform. It can plan and build whole features for you while you watch. Great for beginners. | Free during preview |
| **Cursor** | A VS Code clone with a very strong built-in AI assistant. Very popular. | Free tier, paid ~$20/mo |
| **VS Code + Cline / Continue** | Free AI assistant extensions inside normal VS Code | Free (you bring an API key) |
| **Claude Code** (this AI) | Terminal-based AI that reads your whole repo (it reads `CLAUDE.md` automatically!) | Free tier / paid |

> **My recommendation for you:** Use **Antigravity** or **Cursor** as your main "pair programmer," and keep **Claude Code** around for big-picture reasoning (it already understands your whole project through `CLAUDE.md`). Let the AI write most of the code — your job is to understand it, test it, and guide it.

**One important habit:** whenever you start an AI coding session, tell it: *"Read CLAUDE.md and docs/ first."* That file (which I just created) makes any AI follow your project's rules automatically.

---

## Part 2: Create Free Accounts (Day 1–2)

Sign up for these free services. You'll use them across the project.

### Data (where your stock data comes from — all have free tiers)
- **Alpaca** (alpaca.markets) — **the most important one.** Gives you free stock market data AND free **paper trading** (fake money, real prices). This is perfect for us. Sign up, get your free API keys.
- **yfinance** — a free Python library that downloads Yahoo Finance data. No signup needed. Great for the MVP.
- **FRED** (fred.stlouisfed.org) — free macroeconomic data (interest rates, inflation). Free API key.
- **Financial Modeling Prep** or **Alpha Vantage** — free tiers for company financial data (optional, later).

### Storage & databases (free tiers)
- **Supabase** (supabase.com) or **Neon** (neon.tech) — free hosted PostgreSQL database in the cloud. For the MVP you can also just run Postgres locally with Docker (also free).
- **Cloudflare R2** or **MinIO** (local) — free/cheap object storage for your "data lake." MinIO runs on your own computer for free.

### Compute for training AI models (free)
- **Google Colab** (colab.research.google.com) — free notebooks with **free GPUs** for training models. Huge money-saver.
- **Kaggle** (kaggle.com) — also free GPUs + tons of free financial datasets.
- **Hugging Face** (huggingface.co) — free place to store models and datasets.

### The AI "brain" (for Stage 3, later)
- **Anthropic API** (claude) — for the AI agent reasoning. Costs a little per use, but you get free starter credits. You only need this in Stage 3.
- **Ollama** (ollama.com) — run AI models **locally for free** on your own computer. Good for experimenting without paying.
- **OpenRouter** — one account to access many AI models, often with free options.

### Automation (glue between parts — free)
- **n8n** (n8n.io) — a **visual workflow tool**. Instead of writing code to connect things, you drag boxes and connect them. We'll use it to automate boring jobs (like "every day at 6pm, download new data → clean it → run the model → email me the result"). You can run n8n **free** on your own computer with Docker, or use their free cloud trial. More on this in Part 5.

### Hosting (when you want it online — free tiers)
- **Railway**, **Render**, or **Fly.io** — put your app online cheaply/free.
- **Vercel** — free hosting for your frontend (the website part).
- **GitHub Actions** — free automation for testing/deploying your code.
- **Grafana Cloud** — free tier for dashboards/monitoring.

> **Total cost so far: $0.** Everything above has a free tier big enough to build and demo your project.

---

## Part 3: Build the MVP (Stage 1) — Your First Real Milestone

This is the heart of the guide. We build a **research-only system**: it reads data, makes predictions, and tests them on history — with **no trading at all**, so nothing risky can happen. Follow these steps in order. Don't skip ahead.

### Step 1 — Create the project skeleton
- Open your `stock` folder (this one!) in VS Code.
- Ask your AI helper: *"Read CLAUDE.md. Create the folder structure from CLAUDE.md section 3, with empty starter files."*
- **What you get:** the `libs/`, `backend/`, `datasets/`, `training/`, `backtesting/` folders, etc.
- **Why:** a clean structure now saves huge pain later. The AI will follow the rules in `CLAUDE.md` automatically.

### Step 2 — Download some stock data
- Use **yfinance** (free, no signup) to download a few years of daily prices for, say, 10–20 well-known stocks (Apple, Microsoft, etc.).
- Save it into a `data/` folder on your computer (this folder is already git-ignored so it won't clog your repo).
- **Why:** you need real data to work with. Daily data is small, free, and perfect to start.

### Step 3 — Store it the RIGHT way (point-in-time)
- Load the data into **PostgreSQL** (run it locally with Docker — free).
- **The #1 rule:** every row of data gets TWO dates — when it happened (`event_time`) and when you could have known it (`knowledge_time`). This stops your system from "cheating" by peeking at the future.
- Ask your AI: *"Set up a Postgres table with event_time and knowledge_time columns, and load my price data respecting point-in-time correctness (see docs/claude_ROI.md section 17)."*
- **Why this matters:** this is the single most important idea in the whole project. A system that cheats by looking at the future *looks* amazing in testing and *loses all your money* in reality. Getting this right is what separates a serious project from a toy.

### Step 4 — Create "features" (clues the model learns from)
- A **feature** is a number calculated from the data that might help predict the future — like "the average price over the last 20 days" or "how much the price moved this week."
- Use **pandas** or **polars** (free Python libraries) to calculate a handful of these.
- **Rule:** a feature can only use *past* data (never future). Your AI helper knows this rule from `CLAUDE.md`.
- **Why:** the model doesn't understand raw prices well; features are the meaningful clues it learns patterns from.

### Step 5 — Create "labels" (the answer you're predicting)
- A **label** is the thing you want to predict — for example, "did the stock go up or down over the next 5 days?"
- **Why:** to teach a model, you need examples of "here are the clues (features), and here's what actually happened next (label)."

### Step 6 — Train your first model
- Start with the simplest models: a **linear model** (baseline) and then **LightGBM** (a powerful, beginner-friendly model). Both are free (scikit-learn, LightGBM).
- Train it on the older data. You can do this on your own laptop, or on **Google Colab** (free GPU) if it's slow.
- Track your experiments with **MLflow** (free) so you remember what you tried.
- **Why start simple:** fancy AI models usually do *worse* on financial data than these simple ones, and they're much harder to get right. Always start with the simple baseline.

### Step 7 — Test it HONESTLY (the validation gauntlet)
- This is where most beginners fool themselves. You must test the model on data it has **never seen**, using **walk-forward testing** (train on the past, test on the next period, then roll forward).
- Ask your AI: *"Set up walk-forward validation and purged cross-validation from docs/claude_MLResearchFramework.md section 8. Check for data leakage."*
- **Why:** a model can memorize the past perfectly and still be useless. The only test that matters is: *"would this have worked on data it truly never saw?"* Be your own harshest critic here.

### Step 8 — Backtest a simple strategy
- A **backtest** = pretend you traded using your model's predictions through history, and see if you would have made money — **after** subtracting realistic costs.
- Build a simple backtest engine (or use a free library like `backtesting.py` or `vectorbt` to start).
- **CRITICAL RULE (from CLAUDE.md):** the code that decides trades in the backtest must be the **same code** you'll later use for live trading. Never write it twice. Put it in `libs/` and share it.
- **Why:** if backtest and live use different code, your live results won't match your tests, and you'll lose money on surprises.

### Step 9 — Make a simple report + tiny dashboard
- Create a report showing: did it make money? How risky was it? What was the Sharpe ratio (a "return vs. risk" score)?
- Optional: a small **Streamlit** app (free, super easy) or a simple React page to view results. Streamlit is the fastest way for a beginner to make a data dashboard.
- **Why:** you need to *show* your results — to yourself, to interviewers, to investors.

**🎉 Congratulations — that's the MVP!** You now have a real, honest, research system. This alone is a strong placement project. Put it on GitHub with a nice README.

---

## Part 4: Grow to Paper Trading (Stage 2 / V1)

Once the MVP works, add **fake-money live trading**. Still no real money — but now it runs on live prices in real time.

### Step 10 — Connect to Alpaca paper trading
- Use your free **Alpaca** paper-trading account. It gives you $100,000 of *fake* money and real live prices.
- Your system now: gets a live price → runs the model → the risk checker approves/rejects → sends a *paper* order to Alpaca.
- **Why:** this proves your system works in real time, catching bugs that history-testing can't — with zero risk.

### Step 11 — Add the risk checker (the "kernel")
- Build a simple **risk service** that checks every trade against hard limits: max money per trade, max total exposure, stop if losses get too big.
- **Rule from CLAUDE.md:** the AI can *never* raise these limits itself. Only you can, by hand.
- **Why:** this is your safety net. It's the difference between a controlled system and a runaway one.

### Step 12 — Add explanations + a control panel
- Every trade should come with a plain-English reason ("Bought AAPL because the model was confident and momentum was strong").
- Add a **kill switch** — one button that stops everything.
- **Why:** you must always be able to understand and stop your system. This also impresses interviewers and investors hugely.

### Step 13 — Automate it with n8n
- Now use **n8n** (free) to run things automatically without babysitting:
  - Example workflow: *every trading day at market open → fetch fresh data → update features → run predictions → send to your system → post a summary to your phone/Slack.*
- You build this by dragging and connecting boxes — little to no code.
- **Why:** automation makes it feel like a real product, and frees you from running scripts by hand.

> **After Stage 2 you have:** a system that watches live markets, makes AI-driven paper trades, checks risk, explains itself, and runs automatically. That is a genuinely impressive startup demo.

---

## Part 5: Where Each Special Tool Fits (Quick Map)

You mentioned n8n, Antigravity, and wanting free tools. Here's the clear map of what to use where:

| Tool | Where you use it in AQROS | Free? |
|---|---|---|
| **Antigravity / Cursor / Claude Code** | Writing all the code, with AI help. Your main builder. | Free / cheap |
| **Python + VS Code** | The core language and editor for everything | Free |
| **yfinance / Alpaca / FRED** | Getting market & economic data | Free tiers |
| **PostgreSQL + Docker** | Storing data cleanly (point-in-time) | Free |
| **pandas / polars** | Making features from data | Free |
| **scikit-learn / LightGBM** | The prediction models | Free |
| **Google Colab / Kaggle** | Free GPUs to train models | Free |
| **MLflow** | Tracking your experiments & models | Free |
| **Alpaca paper trading** | Fake-money live trading | Free |
| **n8n** | Automating daily jobs (data → predict → report), connecting services visually | Free (self-host) |
| **Streamlit** | Quick dashboards to show results | Free |
| **GitHub + GitHub Actions** | Storing code + auto-testing | Free |
| **Anthropic API / Ollama** | The AI agents that "reason" (Stage 3 only) | Small cost / free local |
| **Railway / Render / Vercel** | Putting it online for demos | Free tiers |
| **Grafana Cloud** | Monitoring dashboards | Free tier |

> **n8n tip:** think of n8n as the "office manager" that connects your workers. It doesn't do the smart thinking (that's your Python code and models) — it just makes sure the right jobs run at the right time and the results get where they need to go. Great for beginners because it replaces a lot of fiddly "glue" code with drag-and-drop.

---

## Part 6: The AI Brain (Stage 3 / V2) — Only After Stages 1–2 Work

This is the exciting multi-agent part from `docs/claude_aiBrain.md` — a team of AI agents that debate before trading. **Do not start this until Stages 1 and 2 are solid.** It's the most advanced part and only makes sense on top of a working foundation.

Briefly, when you get here:
- Use **Claude models** (via the Anthropic API) or **local models via Ollama** as the "reasoning" for agents.
- Build agents one at a time: start with just an "Analyst" agent and a "Risk Critic" agent that disagree, then add more.
- The agents *propose*; your risk checker from Step 11 still has the final say. **The AI never overrides the safety limits.**
- Add memory (what worked, what failed) so it learns from mistakes.

Keep this stage cheap by: using local models (Ollama) for testing, only calling the paid API when needed, and caching results so you don't re-pay for the same thinking.

---

## Part 7: How to Keep Costs Near Zero

- **Build and test everything locally first** (your laptop + Docker). Cloud only when you need to show it online.
- **Use free GPUs** (Colab/Kaggle) for training — never pay for a GPU while learning.
- **Use free tiers** (Supabase/Neon/Railway/Vercel) — they're generous enough for a demo.
- **Use small data** while building (10–20 stocks, daily prices). Scale up only when it works.
- **Use local AI models (Ollama)** for experimenting; only use paid AI APIs for the final polished demo.
- **Turn things off** when not using them (cloud services often charge for idle time).
- **Realistic budget:** you can build and demo Stages 1–2 for **$0**. Stage 3 with real AI agents might cost **a few dollars to ~$20/month** depending on how much you use the AI API. That's it.

---

## Part 8: A Realistic Timeline for You (Solo Beginner)

Don't rush. Learning is part of the work. A rough, gentle plan:

| Time | Goal |
|---|---|
| **Week 1** | Install tools, create accounts, set up the project skeleton, download data |
| **Week 2–3** | Store data point-in-time correctly, build features and labels |
| **Week 4–5** | Train your first model, validate it honestly (this is the important, careful part) |
| **Week 6–7** | Build the backtest with the shared strategy code, make a report + dashboard → **MVP done!** |
| **Week 8–10** | Connect Alpaca paper trading, add the risk checker + kill switch + explanations |
| **Week 11–12** | Automate with n8n, polish, write a great README → **V1 demo ready!** |
| **Later** | Slowly add the AI agent brain (Stage 3), one agent at a time |

> Even reaching the **MVP (Week 7)** gives you a strong placement project. Reaching **V1 (Week 12)** gives you a real startup demo. Everything beyond is bonus.

---

## Part 9: Tips for Placement & Startup

- **Put it on GitHub with a clean README** that explains what it does, shows a screenshot of your dashboard, and links to the design docs in `docs/`. Recruiters love seeing serious design thinking.
- **Record a 2-minute demo video** showing it working. This is worth more than a hundred lines of description.
- **Be honest about what's built vs. designed.** Say: "I designed the full institutional architecture (in `docs/`) and built a working MVP/paper-trading slice of it." That honesty is impressive and mature.
- **Emphasize the *discipline*, not just the code:** point-in-time correctness, no data leakage, honest validation, the safety risk-kernel. This shows you think like a real quant engineer, not just a coder.
- **The design docs are a huge asset.** Not many beginners can show a 5-document, institutional-grade architecture. Lead with that in interviews.
- **Never claim it makes guaranteed money.** Real quants are humble about this. Talk about the *process* and *rigor*, which is the actual skill.

---

## Part 10: What To Do Right Now (Your Very First Actions)

1. Install **Python, Git, VS Code, Docker** (Part 1).
2. Create a **GitHub account** and push this `stock` folder to it.
3. Create free **Alpaca** and **FRED** accounts (Part 2).
4. Pick your AI helper (**Antigravity** or **Cursor**) and install it.
5. Open this folder, tell your AI: *"Read CLAUDE.md and docs/about.md, then help me start Step 1 of steps_to_Create.md — create the project skeleton."*
6. Then follow Part 3, one step at a time. **Don't skip steps. Make each one work before moving on.**

> **Remember:** you are not building the whole thing at once. You are building one small, honest, working piece — then growing it. Every professional system started exactly this way. You've got this. 🚀

---

### Quick reference: the most important rules (from CLAUDE.md)
1. **Never let data from the future leak into the past** (point-in-time correctness).
2. **Never write your trading logic twice** — share it between backtest and live.
3. **The AI never overrides the risk limits** — only you can, by hand.
4. **Test honestly** — assume every great result is a bug until you prove it isn't.
5. **Never trade real money until paper trading has proven itself** — and even then, start tiny.
6. **Never put passwords or API keys in your code** — use a `.env` file (already git-ignored).

Keep these taped to your monitor. They are what make this a *serious* project.

# Accounting PWA and Template Integration Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Integrate the extracted template into the `x-bot` project as a `FastAPI` service sharing the sqlite db, and build a PWA accounting system with multi-end user binding support and CSV import functionality.

**Architecture:** We are adopting an "API-Centric" architecture. A dedicated FastAPI service acts as the central data manager (sharing `data/bot_data.db` via `aiosqlite`). The frontend PWA connects via REST API, hosted using FastAPI's static file serving. X-Bot will also call this API to automatically log records from user-uploaded images via LLM extraction.

**Tech Stack:** FastAPI, SQLAlchemy (Async), SQLite, Pydantic, Vue3, TailwindCSS, Vite.

---

### Task 1: Integrate Backend Template

**Goal**: Move the backend template code into `src/api`, adapt paths and configurations, and prepare it to run.

**Files:**
- Create: `src/api` (Directory)
- Modify: `pyproject.toml`
- Create/Modify: `src/api/main.py`
- Modify: `src/api/core/database.py`

**Step 1.1**: Move backend source.
Run: `cp -r template/backend/app/* src/api/`
Verify: `ls src/api`

**Step 1.2**: Move models and schemas (create models directory if missing)
Run: 
```bash
# If models/schemas don't exist as folders, just keep the files auth/models.py etc where they are, we'll use them.
mkdir -p src/api/models
mkdir -p src/api/schemas
```

**Step 1.3**: Update root `pyproject.toml` with backend dependencies.
*Add to `dependencies` in `/home/luwei/workspace/x-bot/pyproject.toml`:*
```toml
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0,<0.31.0",
    "pydantic>=2.0.0,<3.0.0",
    "pydantic-settings>=2.0.0,<3.0.0",
    "sqlalchemy[asyncio]>=2.0.0,<2.1.0",
    "fastapi-users[oauth,sqlalchemy]>=15.0.3",
    "passlib[bcrypt]>=1.7.4",
    "python-multipart>=0.0.20,<0.1.0",
    "aiosqlite>=0.22.1",
    "toml>=0.10.2",
```
Run: `uv pip install -e .`

**Step 1.4**: Modify `src/api/core/database.py` to point to the correct DB path and correct import paths.
Replace: `from app.core.config import settings` with `from src.api.core.config import settings` (Fix all `app.` to `src.api.`).
Update database URL generation to ensure it points to `data/bot_data.db` explicitly, or rely on `.env`.
*Change in `src/api/core/database.py`:*
```python
db_url = f"sqlite+aiosqlite:///data/bot_data.db"
```

**Step 1.5**: Modify `src/api/main.py` and other modules to use `src.api.*` instead of `app.*`.
Run: `find src/api -type f -name "*.py" -exec sed -i 's/from app\./from src\.api\./g' {} +`
Run: `find src/api -type f -name "*.py" -exec sed -i 's/import app\./import src\.api\./g' {} +`

**Step 1.6**: Ensure `data/bot_data.db` folder exists.
Run: `mkdir -p data`

**Step 1.7**: Commit backend integration.
Run: `git add src/api pyproject.toml uv.lock && git commit -m "build: integrate backend template into src/api"`


### Task 2: Integrate Frontend Template and Multi-Stage Dockerfile

**Goal**: Move the frontend template code, setup Vite to build to the FastAPI static directory, and update `Dockerfile` & `docker-compose.yml`.

**Files:**
- Delete: `src/platforms/web`
- Create: `src/platforms/web` (from template)
- Modify: `src/platforms/web/vite.config.ts`
- Modify: `src/api/main.py` (Add static mounting)
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

**Step 2.1**: Replace old web platform with new frontend template.
Run: `rm -rf src/platforms/web && cp -r template/frontend src/platforms/web`

**Step 2.2**: Update `src/platforms/web/vite.config.ts` for build paths.
Update `outDir`:
```typescript
    outDir: '../../../api/static/dist',
```
Update `base`:
```typescript
  base: '/',
```

**Step 2.3**: Update `src/api/main.py` to serve static files.
Add imports: 
```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
```
Add routing before `if __name__ == "__main__":`:
```python
# Create static wrapper for SPA fallback
static_dir = os.path.join(os.path.dirname(__file__), "static/dist")
os.makedirs(static_dir, exist_ok=True)

app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Try to serve requested file
    file_path = os.path.join(static_dir, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    # SPA Fallback
    return FileResponse(os.path.join(static_dir, "index.html"))
```

**Step 2.4**: Update `Dockerfile` to handle frontend build.
*Add multi-stage build content at top of `Dockerfile`:*
```dockerfile
# Build Frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY src/platforms/web ./src/platforms/web
WORKDIR /app/src/platforms/web
# Temporary remove overriding rolldown as it fails in basic npm install without full context
RUN sed -i '/"overrides"/,+3d' package.json
RUN npm install
RUN npm run build
```
In python stage, copy dist:
```dockerfile
COPY --from=frontend-builder /app/src/api/static/dist /app/src/api/static/dist
```
*Wait, outDir goes up 3 levels, so in docker it builds to `/app/src/api/static/dist`. We just copy it.*

**Step 2.5**: Update `docker-compose.yml` to run the API container.
*Add service:*
```yaml
  x-bot-api:
    build: .
    container_name: x-bot-api
    env_file:
      - .env
    restart: unless-stopped
    network_mode: "host"
    command: [ "uv", "run", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000" ]
    volumes:
      - ./data:/app/data
      - ./config:/app/config
```

**Step 2.6**: Commit Docker changes.
Run: `git add src/platforms/web src/api/main.py Dockerfile docker-compose.yml && git commit -m "build: setup multi-stage docker and static file serving for API"`


### Task 3: Platform User Binding Models and Logic

**Goal**: Allow users to link their web accounts with bot accounts (e.g. Telegram IDs).

**Files:**
- Create: `src/api/models/binding.py`
- Modify: `src/api/core/database.py` (Include new models for init)
- Create: `src/api/routers/binding_router.py`
- Modify: `src/api/main.py` (Include router)

**Step 3.1**: Create `PlatformUserBinding` model.
*Create `src/api/models/binding.py`:*
```python
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped
from src.api.core.database import Base

class PlatformUserBinding(Base):
    __tablename__ = "platform_user_bindings"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False) # e.g., 'telegram'
    platform_user_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
```

**Step 3.2**: Register Model in Database Init.
Modify `src/api/core/database.py`:
```python
    from src.api.auth.models import User, OAuthAccount
    from src.api.models.binding import PlatformUserBinding # Add this line
```

**Step 3.3**: Create API to verify and save bindings.
*Create `src/api/routers/binding_router.py`:*
(Implement HTTP APIs required to do bindings, or handle it mostly through the Bot logic using Python code directly if preferable). Let's implement at least `GET /api/v1/binding/me` to list bindings under Auth constraint.

**Step 3.4**: Include Router in `main.py`.
```python
from src.api.routers.binding_router import router as binding_router
app.include_router(binding_router, prefix="/api/v1/binding", tags=["binding"])
```

**Step 3.5**: Commit.
Run: `git add src/api/models/binding.py src/api/core/database.py src/api/routers/binding_router.py src/api/main.py && git commit -m "feat(api): add platform user binding model and api"`


### Task 4: Accounting Core Models

**Goal**: Create tables for Books, Categories, Accounts, and Records based on requirement.

**Files:**
- Create: `src/api/models/accounting.py`
- Modify: `src/api/core/database.py`

**Step 4.1**: Create Accounting models.
*Create `src/api/models/accounting.py`:*
```python
from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, DateTime
from sqlalchemy.orm import mapped_column, Mapped, relationship
from datetime import datetime
from src.api.core.database import Base

class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. 现金, 支付宝

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False) # income, expense, transfer
    parent_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=True)

class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    target_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=True) # For transfers
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=True)
    record_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    payee: Mapped[str] = mapped_column(String(100), nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
```

**Step 4.2**: Register Model in Database Init.
Modify `src/api/core/database.py` to import `src.api.models.accounting`.

**Step 4.3**: Commit.
Run: `git add src/api/models/accounting.py src/api/core/database.py && git commit -m "feat(api): add accounting models"`


### Task 5: Accounting API & CSV Import

**Goal**: Implement endpoints to fetch records and import data from a Mint (薄荷) CSV.

**Files:**
- Create: `src/api/routers/accounting_router.py`
- Modify: `src/api/main.py`

**Step 5.1**: Build basic CRUD endpoints.
Implement `GET /api/v1/accounting/records` supporting filter by `book_id`.

**Step 5.2**: Build CSV import endpoint.
Implement `POST /api/v1/accounting/import/csv`.
Use `csv.reader` to parse `类型, 货币, 金额, 汇率, 项目(Book), 分类, 父类, 账户, 付款, 收款, 商家, 时间, 标签, 作者, 备注`.
Logic: Map CSV string names to Database IDs (Account.name -> account_id), creating them if they don't exist.

**Step 5.3**: Commit.
Run: `git add src/api/routers/accounting_router.py src/api/main.py && git commit -m "feat(api): implement accounting crud and csv import api"`


### Task 6: PWA Frontend Fundamentals & UI

**Goal**: Make the web app a PWA, add login page, and dashboard page.

**Files:**
- Modify: `src/platforms/web/index.html` (Manifest)
- Create: `src/platforms/web/public/manifest.webmanifest`
- Create/Modify: Vue components.

**Step 6.1**: Setup PWA Manifest.
Add `manifest.webmanifest` in `public` and link it in `index.html`.

**Step 6.2**: Commit.
Run: `git add src/platforms/web && git commit -m "feat(web): build pwa frontend for accounting dashboard"`


### Task 7: X-Bot LLM Integration for "Quick Add"

**Goal**: Allow X-Bot to call a tool/function to insert a record based on chat input.

**Files:**
- Modify: Bot tools.

**Step 7.1**: Implement the Function Call logic in Python.
Function `add_accounting_record` that inserts into `Record` table using `get_async_session()`.

**Step 7.2**: Expose it to the Agent.
Register `add_accounting_record` tool descriptor.

**Step 7.3**: Commit.
Run: `git commit -m "feat(bot): add LLM tool for automatic quick accounting"`

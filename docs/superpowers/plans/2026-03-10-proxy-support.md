# Proxy Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add standard HTTP(S) proxy support to the OpenRouter-compatible client and expose it in the desktop UI.

**Architecture:** Extend `OpenAICompatibleSettings` with a single optional `proxy_url` that prefers explicit UI configuration over environment variables. Route requests through a `urllib` opener with `ProxyHandler` only when a proxy is configured, and keep existing request behavior unchanged otherwise.

**Tech Stack:** Python, `urllib.request`, PySide6, `unittest`

---

## Chunk 1: Client Proxy Support

### Task 1: Add failing client tests for proxy configuration

**Files:**
- Modify: `tests/test_image_generation_client.py`
- Modify: `sts_draw/image_generation_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_uses_explicit_proxy_url_when_present():
    ...

def test_reads_proxy_from_environment_when_setting_missing():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_image_generation_client.OpenAICompatibleClientTests.test_uses_explicit_proxy_url_when_present tests.test_image_generation_client.OpenAICompatibleClientTests.test_reads_proxy_from_environment_when_setting_missing -v`
Expected: FAIL because no proxy setting/opener support exists yet.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class OpenAICompatibleSettings:
    ...
    proxy_url: str | None = None

def _build_urlopen(self):
    if not self.settings.proxy_url:
        return request.urlopen
    opener = request.build_opener(request.ProxyHandler({"http": proxy, "https": proxy}))
    return opener.open
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_image_generation_client.OpenAICompatibleClientTests.test_uses_explicit_proxy_url_when_present tests.test_image_generation_client.OpenAICompatibleClientTests.test_reads_proxy_from_environment_when_setting_missing -v`
Expected: PASS

## Chunk 2: UI Proxy Configuration

### Task 2: Add failing UI test for proxy input wiring

**Files:**
- Modify: `tests/test_ui.py`
- Modify: `sts_draw/ui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_window_exposes_proxy_input():
    ...

def test_generate_line_art_updates_proxy_setting():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_ui.MainWindowFactoryTests.test_window_exposes_proxy_input tests.test_ui.MainWindowFactoryTests.test_generate_line_art_updates_proxy_setting -v`
Expected: FAIL because no proxy field exists yet.

- [ ] **Step 3: Write minimal implementation**

```python
self.proxy_input = QtWidgets.QLineEdit()
form_layout.addRow("代理地址", self.proxy_input)
controller.image_generation_client.settings.proxy_url = self.proxy_input.text().strip() or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_ui.MainWindowFactoryTests.test_window_exposes_proxy_input tests.test_ui.MainWindowFactoryTests.test_generate_line_art_updates_proxy_setting -v`
Expected: PASS

## Chunk 3: Regression Verification

### Task 3: Verify full suite

**Files:**
- Verify: `tests/test_image_generation_client.py`
- Verify: `tests/test_ui.py`

- [ ] **Step 1: Run focused client tests**

Run: `python -m unittest tests.test_image_generation_client -v`
Expected: PASS

- [ ] **Step 2: Run focused UI tests**

Run: `python -m unittest tests.test_ui -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS

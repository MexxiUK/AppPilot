import pytest

from apppilot.browser_driver import BrowserDriver


@pytest.mark.asyncio
async def test_extract_simple_text() -> None:
    driver = BrowserDriver()
    await driver.start("data:text/html,<h1>Hello</h1><p class='price'>$10</p>")
    try:
        result = await driver._extract({"title": "h1", "price": ".price"})
        assert result == {"title": "Hello", "price": "$10"}
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_extract_count_and_exists() -> None:
    driver = BrowserDriver()
    await driver.start(
        "data:text/html,<ul><li class='item'>a</li><li class='item'>b</li></ul>"
    )
    try:
        result = await driver._extract({
            "count": {"selector": ".item", "attribute": "count"},
            "has_header": {"selector": "h1", "attribute": "exists"},
        })
        assert result == {"count": 2, "has_header": False}
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_extract_multiple_nested() -> None:
    driver = BrowserDriver()
    html = (
        "<div class='product'><span class='name'>A</span>"
        "<span class='price'>1</span></div>"
        "<div class='product'><span class='name'>B</span>"
        "<span class='price'>2</span></div>"
    )
    await driver.start(f"data:text/html,{html}")
    try:
        result = await driver._extract({
            "items": {
                "selector": ".product",
                "multiple": True,
                "fields": {"name": ".name", "price": ".price"},
            }
        })
        assert result == {"items": [{"name": "A", "price": "1"}, {"name": "B", "price": "2"}]}
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_extract_empty_schema() -> None:
    driver = BrowserDriver()
    await driver.start("data:text/html,<h1>Hello</h1>")
    try:
        result = await driver._extract({})
        assert result == {}
        result = await driver._extract(None)
        assert result == {}
    finally:
        await driver.stop()

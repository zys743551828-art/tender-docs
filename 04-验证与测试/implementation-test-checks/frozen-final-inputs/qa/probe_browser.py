"""系统Chromium实际UI任务，独立QA项目和合成PDF；不使用请求拦截假后端。"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from qa.helpers import ROOT, ensure, key, members, pdf_bytes, three_page_pdf, write_result


def main():
    out = Path(os.environ.get("QA_BROWSER_OUTPUT", "qa/browser-results"))
    out.mkdir(parents=True, exist_ok=True)
    result = dict(
        scope="实际Chromium与QA API/S3/Kafka的单PDF资料UI；无模型评审/真人使用结论",
        passed=False,
        started_at=datetime.now(timezone.utc).isoformat(),
        checks=[],
        requests_failed=[],
        model_executed=False,
    )
    member = members()[0]
    stage = "启动"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        context = browser.new_context(viewport=dict(width=1440, height=1000))
        page = context.new_page()
        page.set_default_timeout(35000)
        page.on(
            "response",
            lambda r: result["requests_failed"].append(
                dict(path=urlsplit(r.url).path, status=r.status)
            )
            if r.status >= 400 and urlsplit(r.url).path != "/api/v1/auth/session"
            else None,
        )
        try:
            stage = "登录"
            page.goto("http://127.0.0.1:5174/", wait_until="networkidle")
            page.get_by_label("账号", exact=True).fill(member["login"])
            page.get_by_label("密码", exact=True).fill(member["password"])
            page.get_by_role("button", name="登录工作区", exact=True).click()
            page.get_by_role("heading", name="投标项目", exact=True).wait_for()
            result["checks"].append("真实浏览器Cookie登录")
            stage = "建项目"
            title = "浏览器独立QA-" + key()
            page.get_by_role("button", name="＋ 新建投标", exact=True).click()
            page.get_by_label("项目名称", exact=True).fill(title)
            page.get_by_role("button", name="创建并上传资料", exact=False).click()
            page.get_by_role("heading", name=title, exact=True).wait_for()
            result["project_path"] = urlsplit(page.url).path
            result["checks"].append("不经模板/目录即可建项目")
            stage = "上传并核验"
            payload = three_page_pdf()
            page.locator("#project-files").set_input_files(
                dict(name="browser-synthetic.pdf", mimeType="application/pdf", buffer=payload)
            )
            page.get_by_label("browser-synthetic.pdf的资料用途", exact=True).select_option("body")
            page.get_by_role("button", name="上传并核验原件", exact=False).click()
            confirm = page.get_by_role("button", name=re.compile(r"确认\s+1\s+份资料入库"))
            confirm.wait_for()
            ensure(confirm.is_enabled(), "verified后允许确认真实入库")
            confirm.click()
            page.get_by_role("button", name="查看原件与依据", exact=False).wait_for()
            page.get_by_text("解析范围不完整", exact=True).first.wait_for()
            result["checks"].append("浏览器真实S3 CORS上传、Worker核验和资料入库")
            stage = "原页与恢复"
            open_button = page.get_by_role("button", name="查看原件与依据", exact=False)
            open_button.click()
            page.get_by_role("dialog").wait_for()
            page.get_by_text("以下范围尚未完整核对", exact=True).wait_for()
            pixels = []
            for physical_page in [1, 2, 3]:
                selector = page.get_by_role("dialog").get_by_role("combobox")
                selector.locator('option[value="3"]').wait_for(state="attached")
                selector.select_option(str(physical_page))
                ensure(selector.input_value() == str(physical_page), "实际物理页选择值正确")
                canvas = page.get_by_role(
                    "img", name=f"browser-synthetic.pdf，物理页{physical_page}原页", exact=True
                )
                canvas.wait_for()
                page.get_by_text("正在绘制PDF原页…", exact=True).wait_for(state="hidden")
                stats = canvas.evaluate(
                    """el=>{let d=el.getContext('2d').getImageData(0,0,el.width,el.height).data;let opaque=0,dark=0;for(let i=0;i<d.length;i+=4){if(d[i+3]>0)opaque++;if(d[i]+d[i+1]+d[i+2]<600&&d[i+3]>0)dark++;}return {width:el.width,height:el.height,opaque,dark}}"""
                )
                ensure(
                    stats["width"] > 0 and stats["height"] > 0 and stats["opaque"] > 0,
                    "物理页必须实际画入canvas",
                )
                ensure(
                    stats["dark"] > 0 if physical_page in [1, 3] else stats["dark"] == 0,
                    "文字/图像非白像素与空白页分别正确",
                )
                pixels.append(dict(physical_page=physical_page, **stats))
            result["rendered_pages"] = pixels
            result["checks"].append("独立三页夹具的文字/空白/栅格页均导航并实际绘制像素")
            ensure(
                page.get_by_role("button", name="下载资料原件", exact=False).is_visible(),
                "下载用途明确为资料原件",
            )
            with page.expect_download() as download_event:
                page.get_by_role("button", name="下载资料原件", exact=False).click()
            download = download_event.value
            download.save_as(out / "downloaded-original.pdf")
            ensure(
                hashlib.sha256((out / "downloaded-original.pdf").read_bytes()).hexdigest()
                == hashlib.sha256(payload).hexdigest(),
                "真实浏览器下载与上传原件SHA一致",
            )
            ensure(download.suggested_filename == "browser-synthetic.pdf", "取件保留原文件名")
            result["checks"].append("真正触发浏览器取件，原文件名/字节SHA/用途一致")
            page.screenshot(path=str(out / "desktop-evidence.png"), full_page=True)
            page.keyboard.press("Escape")
            page.get_by_role("dialog").wait_for(state="hidden")
            ensure(
                open_button.evaluate("(el)=>el===document.activeElement"),
                "关闭原页返回来源按钮焦点",
            )
            page.reload(wait_until="networkidle")
            page.get_by_role("heading", name=title, exact=True).wait_for()
            page.get_by_role("button", name="查看原件与依据", exact=False).wait_for()
            result["checks"].append("原页/缺口/用途可见，Escape焦点返回，刷新恢复已保存资料")
            stage = "窄屏键盘"
            page.set_viewport_size(dict(width=390, height=844))
            ensure(
                page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"),
                "390px页面无横向溢出",
            )
            page.get_by_role("button", name="查看原件与依据", exact=False).focus()
            page.keyboard.press("Enter")
            page.get_by_role("dialog").wait_for()
            ensure(
                page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"),
                "390px来源面板无横向溢出",
            )
            page.screenshot(path=str(out / "mobile-evidence.png"), full_page=True)
            page.keyboard.press("Escape")
            result["checks"].append("390px与键盘可打开/关闭原页且保持单层阅读")
            stage = "历史版本"
            payload_v2 = pdf_bytes("Independent browser QA revision two different official body.")
            page.locator("#project-files").set_input_files(
                dict(name="browser-v2.pdf", mimeType="application/pdf", buffer=payload_v2)
            )
            page.get_by_label("browser-v2.pdf的资料用途", exact=True).select_option("body")
            page.get_by_label("browser-v2.pdf的文件身份", exact=True).select_option(
                label="browser-synthetic.pdf · 新版本"
            )
            page.get_by_role("button", name="上传并核验原件", exact=False).click()
            page.get_by_role("button", name=re.compile(r"确认\s+1\s+份资料入库")).click()
            history = page.get_by_role("button", name="查看历史版本", exact=True)
            history.wait_for()
            history.scroll_into_view_if_needed()
            box = history.bounding_box()
            ensure(
                box and box["x"] >= 0 and box["x"] + box["width"] <= 391,
                "390px历史入口处于可视横向范围",
            )
            bid_id = result["project_path"].split("/")[2]
            metadata = page.evaluate(
                """async bid=>{
                const r=await fetch('/api/v1/bids/'+bid+'/materials');
                if(!r.ok)throw Error('materials');return r.json();}""",
                bid_id,
            )
            docs = metadata["documents"]
            head = next(d for d in docs if d["filename"] == "browser-v2.pdf")
            ensure(
                head["file_revision"] == 2 and head["file_head_version_id"] == head["id"],
                "二版真实元数据为当前head",
            )
            old = page.evaluate(
                """async id=>{
                const r=await fetch('/api/v1/documents/'+id);
                if(!r.ok)throw Error('history');return r.json();}""",
                head["previous_version_id"],
            )
            ensure(
                old["file_revision"] == 1
                and old["file_id"] == head["file_id"]
                and old["file_head_version_id"] == head["id"],
                "历史身份与当前head关系正确",
            )
            history.click()
            panel = page.get_by_role("region", name="文件历史版本", exact=True)
            panel.get_by_role("button", name="查看此历史原件与依据", exact=False).click()
            selector = page.get_by_role("dialog").get_by_role("combobox")
            selector.locator('option[value="3"]').wait_for(state="attached")
            selector.select_option("3")
            old_canvas = page.get_by_role(
                "img", name="browser-synthetic.pdf，物理页3原页", exact=True
            )
            old_canvas.wait_for()
            page.get_by_text("正在绘制PDF原页…", exact=True).wait_for(state="hidden")
            ensure(
                old_canvas.evaluate(
                    "el=>{let a=el.getContext('2d').getImageData(0,0,el.width,el.height).data;for(let i=0;i<a.length;i+=4)if(a[i]+a[i+1]+a[i+2]<600&&a[i+3])return true;return false;}"
                ),
                "历史第三页真实图像像素",
            )
            with page.expect_download() as old_download_event:
                page.get_by_role("button", name="下载资料原件", exact=False).click()
            old_download = old_download_event.value
            old_download.save_as(out / "downloaded-history.pdf")
            ensure(
                old_download.suggested_filename == "browser-synthetic.pdf"
                and hashlib.sha256((out / "downloaded-history.pdf").read_bytes()).digest()
                == hashlib.sha256(payload).digest(),
                "历史下载仍是V1原名和原字节",
            )
            ensure(
                page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"),
                "390px历史来源面板无横向溢出",
            )
            result["history"] = dict(
                current_id=head["id"],
                previous_id=old["id"],
                file_id=head["file_id"],
                current_revision=2,
                previous_revision=1,
                historical_sha256=hashlib.sha256(payload).hexdigest(),
            )
            page.screenshot(path=str(out / "mobile-history.png"), full_page=True)
            result["checks"].append(
                "真实UI二版→元数据head/previous→历史V1第三页canvas和原名SHA取件，390px入口可达"
            )
            result["passed"] = True
        except Exception as e:
            result["failure"] = dict(
                stage=stage,
                type=type(e).__name__,
                message="真实浏览器任务未通过；定位与截屏见该次证据，不输出口令或签名URL",
            )
            page.screenshot(path=str(out / "failed-state.png"), full_page=True)
        finally:
            result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            result["frontend_sources"] = {
                str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in (ROOT / "apps/web/src").glob("*")
                if f.is_file()
            }
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_result(out / "result.json", result)
            browser.close()
    print(
        dict(passed=result["passed"], checks=len(result["checks"]), failure=result.get("failure"))
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

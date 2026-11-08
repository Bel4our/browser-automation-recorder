import os
import time
import math
import random
from pathlib import Path
from playwright.sync_api import sync_playwright

PATH_TO_VIDEO_FOLDER = "./video"

INJECT_EFFECTS_SCRIPT = """
(() => {
    window.__setCursor = (x, y) => {
        const c = document.getElementById('__pw_cursor__');
        const h = document.getElementById('__pw_halo__');
        if (c && h) {
            c.style.display = 'block';
            h.style.display = 'block';
            c.style.left = x + 'px';
            c.style.top = y + 'px';
            h.style.left = x + 'px';
            h.style.top = y + 'px';
        }
    };

    window.__ShowCallout = (text, bgColor = '#FFEB3B', textColor = '#000000', fadeSeconds = 2.0) => {
        const callout = document.getElementById('__pw_callout__');
        if (!callout) return;
        callout.innerText = text;
        callout.style.backgroundColor = bgColor;
        callout.style.color = textColor;
        callout.style.fontSize = '16pt';
        callout.style.display = 'inline-block';
        callout.style.transition = `opacity ${fadeSeconds}s ease-in-out`;
        
        requestAnimationFrame(() => {
            callout.style.opacity = '1';
        });

        setTimeout(() => {
            callout.style.opacity = '0';
            setTimeout(() => {
                callout.style.display = 'none';
            }, fadeSeconds * 1000);
        }, fadeSeconds * 1000 + 800);
    };

    function initVisuals() {
        if (document.getElementById('__pw_layer__')) return;
        const target = document.documentElement;
        if (!target) return;

        const layer = document.createElement('div');
        layer.id = '__pw_layer__';
        layer.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483647;overflow:hidden;';

        const style = document.createElement('style');
        style.textContent = `
            @keyframes pw-ripple {
                0% { transform: translate(-50%, -50%) scale(0.1); opacity: 0.9; }
                100% { transform: translate(-50%, -50%) scale(1.6); opacity: 0; }
            }
            .pw-ripple-wave {
                position: absolute;
                width: 50px;
                height: 50px;
                border-radius: 50%;
                border: 3px solid #0066FF;
                background: rgba(0, 102, 255, 0.35);
                pointer-events: none;
                animation: pw-ripple 0.5s ease-out forwards;
            }
        `;
        (document.head || target).appendChild(style);

        const halo = document.createElement('div');
        halo.id = '__pw_halo__';
        halo.style.cssText = 'position:absolute;width:64px;height:64px;border-radius:50%;background:rgba(255,230,0,0.5);border:1px solid rgba(200,180,0,0.7);transform:translate(-32px,-32px);pointer-events:none;display:none;';

        const cursor = document.createElement('div');
        cursor.id = '__pw_cursor__';
        cursor.style.cssText = 'position:absolute;width:32px;height:32px;pointer-events:none;display:none;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5));';
        cursor.innerHTML = `
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M2 2L2 26L8.5 20.5L13.5 30.5L17.5 28.5L12.5 18.5L20 18.5L2 2Z" 
                      fill="white" stroke="black" stroke-width="2.2" stroke-linejoin="round"/>
            </svg>
        `;

        const callout = document.createElement('div');
        callout.id = '__pw_callout__';
        callout.style.cssText = 'position:fixed;top:25px;left:50%;transform:translateX(-50%);padding:10px 22px;border-radius:6px;font-family:Arial,sans-serif;font-weight:bold;box-shadow:0 4px 15px rgba(0,0,0,0.25);opacity:0;display:none;pointer-events:none;white-space:nowrap;';

        layer.appendChild(halo);
        layer.appendChild(cursor);
        layer.appendChild(callout);
        target.appendChild(layer);
    }

    initVisuals();
    window.addEventListener('DOMContentLoaded', initVisuals);

    window.addEventListener('mousemove', (e) => {
        window.__setCursor(e.clientX, e.clientY);
    }, { capture: true, passive: true });

    window.addEventListener('mousedown', (e) => {
        initVisuals();
        const layer = document.getElementById('__pw_layer__');
        if (!layer) return;
        const ripple = document.createElement('div');
        ripple.className = 'pw-ripple-wave';
        ripple.style.left = e.clientX + 'px';
        ripple.style.top = e.clientY + 'px';
        layer.appendChild(ripple);
        setTimeout(() => ripple.remove(), 550);
    }, { capture: true, passive: true });
})();
"""

def ShowCallout(page, text: str, bg_color: str = "#FFEB3B", text_color: str = "#000000", fade: float = 2.0):
    try:
        page.evaluate(f"window.__ShowCallout && window.__ShowCallout('{text}', '{bg_color}', '{text_color}', {fade});")
    except Exception:
        pass


def smooth_move(page, start_x, start_y, end_x, end_y, steps=35, sleep_time=0.015):
    for i in range(1, steps + 1):
        t = i / steps
        ease = 0.5 - 0.5 * math.cos(math.pi * t)
        curr_x = start_x + (end_x - start_x) * ease
        curr_y = start_y + (end_y - start_y) * ease
        page.mouse.move(curr_x, curr_y)
        time.sleep(sleep_time)


def human_type(page, text: str):
    for char in text:
        page.keyboard.type(char)
        if char == " ":
            time.sleep(random.uniform(0.18, 0.32))
        elif random.random() < 0.12:
            time.sleep(random.uniform(0.20, 0.35))
        else:
            time.sleep(random.uniform(0.06, 0.13))


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    video_dir = Path(PATH_TO_VIDEO_FOLDER).resolve()
    video_dir.mkdir(parents=True, exist_ok=True)

    cur_x, cur_y = 120, 120

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        
        context = browser.new_context(
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale=language
        )

        context.add_init_script(INJECT_EFFECTS_SCRIPT)
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.mouse.move(cur_x, cur_y)
            time.sleep(0.8)

            query = f"Magento developer in {city}"
            ShowCallout(page, query, bg_color="#FFEB3B", text_color="#000000", fade=1.5)
            time.sleep(1.2)

            search_input = page.wait_for_selector('textarea[name="q"]', timeout=15000)
            box = search_input.bounding_box()
            target_x = box["x"] + box["width"] / 3
            target_y = box["y"] + box["height"] / 2

            smooth_move(page, cur_x, cur_y, target_x, target_y, steps=35)
            cur_x, cur_y = target_x, target_y
            page.mouse.click(cur_x, cur_y)
            time.sleep(0.4)

            human_type(page, query)
            time.sleep(0.4)

            submit_locator = page.locator('form button[type="submit"]:visible, button[type="submit"]:visible, button[aria-label="Search"]:visible').first
            submit_locator.wait_for(state="visible", timeout=5000)
            s_box = submit_locator.bounding_box()

            if s_box:
                btn_x = s_box["x"] + s_box["width"] / 2
                btn_y = s_box["y"] + s_box["height"] / 2
            else:
                btn_x = box["x"] + box["width"] - 25
                btn_y = box["y"] + box["height"] / 2

            smooth_move(page, cur_x, cur_y, btn_x, btn_y, steps=25)
            cur_x, cur_y = btn_x, btn_y
            page.mouse.click(cur_x, cur_y)

            result_selector = 'a[data-testid="result-title-a"]'
            first_result = page.wait_for_selector(result_selector, timeout=25000)
            
            page.mouse.move(cur_x, cur_y)
            time.sleep(1.2)

            r_box = first_result.bounding_box()
            if r_box:
                res_x = r_box["x"] + min(70, r_box["width"] / 4)
                res_y = r_box["y"] + r_box["height"] / 2
                smooth_move(page, cur_x, cur_y, res_x, res_y, steps=40)
                cur_x, cur_y = res_x, res_y
                page.mouse.click(cur_x, cur_y)
                time.sleep(3.0)

        except Exception as e:
            print(f"Error: {company_name}: {e}")
            return None
        finally:
            video_ref = page.video
            context.close()
            browser.close()

        if video_ref:
            raw_video_path = Path(video_ref.path())
            sanitized_name = "".join(c for c in company_name if c.isalnum() or c in ("_", "-")).rstrip()
            final_video_path = video_dir / f"{sanitized_name}.webm"

            if final_video_path.exists():
                final_video_path.unlink()

            raw_video_path.rename(final_video_path)
            return str(final_video_path.resolve())

        return None
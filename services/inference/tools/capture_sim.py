"""conveyor-cookie-sim.html 을 헤드리스 브라우저로 돌려 프레임 PNG + 정답 라벨 JSON 을 저장한다.

준비: pip install playwright && python -m playwright install chromium
실행: python tools/capture_sim.py --html ../../../conveyor-cookie-sim.html --frames 30 --strength 55
결과: data/sim/0000.png, 0000.json, ...
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def step(page, n):
    page.evaluate("n => { for (let i = 0; i < n; i++) document.querySelector('#step').click() }", n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "sim"))
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--steps", type=int, default=20, help="저장 사이에 진행할 시뮬레이터 프레임 수")
    ap.add_argument("--strength", type=int, default=55, help="불량 강도 슬라이더 (10~100)")
    ap.add_argument("--defect", type=int, default=30, help="불량률 슬라이더 (0~100)")
    ap.add_argument("--scale", type=float, default=1, help="devicePixelRatio. 2 면 과자가 두 배 크게 찍힌다")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 800}, device_scale_factor=args.scale,
                                accept_downloads=True)
        page.goto(Path(args.html).resolve().as_uri())
        page.click("#run")  # 일시정지
        for name in ("strength", "defect"):
            page.eval_on_selector(f"#{name}", "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')) }",
                                  getattr(args, name))
        page.click("#reset")
        step(page, 300)  # 화면이 과자로 찰 때까지

        for i in range(args.frames):
            step(page, args.steps)
            for button, ext in (("#shot", "png"), ("#labels", "json")):
                with page.expect_download() as d:
                    page.click(button)
                d.value.save_as(out / f"{i:04d}.{ext}")
        browser.close()
    print(f"{args.frames}개 프레임 저장: {out}")


if __name__ == "__main__":
    main()

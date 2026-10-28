"""컨베이어 시뮬레이터(conveyor-cookie-sim.html)를 브라우저로 자동 조작해서
프레임 PNG + 정답 라벨 JSON 을 연속으로 저장한다.

준비: pip install playwright   (브라우저는 PC 에 설치된 Edge 를 사용)
실행: python tests/capture_sim.py --html 경로/conveyor-cookie-sim.html --frames 40
결과: tests/sim_frames/0000.png, 0000.json, 0001.png, ...
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", default=str(Path(__file__).parent / "sim_frames"))
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--steps", type=int, default=6, help="저장 사이에 진행할 시뮬레이터 프레임 수 (30fps 기준 6 = 초당 5장 샘플링)")
    ap.add_argument("--channel", default="msedge", help="msedge 또는 chrome")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 800}, device_scale_factor=1, accept_downloads=True)
        page.goto(Path(args.html).resolve().as_uri())
        page.click("#run")    # 일시정지
        page.click("#reset")  # 라인 비우고 처음부터
        # 화면이 과자로 찰 때까지 미리 진행
        page.evaluate("n => { for (let i = 0; i < n; i++) document.querySelector('#step').click() }", 300)

        for i in range(args.frames):
            page.evaluate("n => { for (let i = 0; i < n; i++) document.querySelector('#step').click() }", args.steps)
            for button, ext in (("#shot", "png"), ("#labels", "json")):
                with page.expect_download() as d:
                    page.click(button)
                d.value.save_as(out / f"{i:04d}.{ext}")
        browser.close()
    print(f"{args.frames}개 프레임 저장: {out}")


if __name__ == "__main__":
    main()

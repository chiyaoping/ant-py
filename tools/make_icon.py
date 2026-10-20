#!/usr/bin/env python3
"""把 assets/icon.svg 渲染成多尺寸 assets/icon.ico。

这是**开发时**脚本，不参与打包运行。需要 Node + resvg-js：
    cd .icontmp && npm install @resvg/resvg-js
    python tools/make_icon.py

生成的 icon.ico 直接提交进仓库，因此 build.bat 只依赖 PyInstaller，
不需要每次打包都装 Node。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG = os.path.join(ROOT, "assets", "icon.svg")
ICO = os.path.join(ROOT, "assets", "icon.ico")
NODE_DIR = os.path.join(ROOT, ".icontmp")

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main():
    if not os.path.isfile(SVG):
        sys.exit(f"missing {SVG}")
    if not os.path.isdir(os.path.join(NODE_DIR, "node_modules")):
        sys.exit(f"run first:  cd {NODE_DIR} && npm install @resvg/resvg-js")

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        # 脚本必须放在 .icontmp/ 下：ESM 的裸模块名按脚本自身位置解析，
        # 放在临时目录里会找不到 node_modules。
        js = os.path.join(NODE_DIR, "_r.mjs")
        with open(js, "w", encoding="utf-8") as f:
            f.write(
                "import { Resvg } from '@resvg/resvg-js';\n"
                "import fs from 'fs';\n"
                "import path from 'path';\n"
                f"const svg = fs.readFileSync({SVG!r}, 'utf8');\n"
                f"for (const s of {SIZES!r}) {{\n"
                "  const r = new Resvg(svg, { fitTo: { mode: 'width', value: s } });\n"
                "  fs.writeFileSync(path.join(process.argv[2], `png_${s}.png`), r.render().asPng());\n"
                "}\n"
                "console.log('ok');\n"
            )
        try:
            subprocess.run(["node", js, tmp], cwd=NODE_DIR, check=True)
        except subprocess.CalledProcessError:
            sys.exit("resvg render failed — see node output above")
        finally:
            os.remove(js)

        # PIL 的 ICO 存储需要显式指定各尺寸，否则只会写入单张图
        base = Image.open(os.path.join(tmp, f"png_{max(SIZES)}.png")).convert("RGBA")
        frames = [Image.open(os.path.join(tmp, f"png_{s}.png")).convert("RGBA") for s in SIZES]
        base.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames)

    size = os.path.getsize(ICO)
    print(f"wrote {ICO} ({size} bytes, sizes={SIZES})")


if __name__ == "__main__":
    main()

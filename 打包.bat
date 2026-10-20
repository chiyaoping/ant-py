@echo off
rem ============================================================
rem  ant-py 一键打包
rem  输入: gui.py + bin/ + nodes.json + bypass_list.txt + assets/icon.ico
rem  输出: dist/ant-py.exe (单文件, 约 80MB)
rem
rem  依赖: pip install pyinstaller pillow pystray
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/3] 检查 PyInstaller ...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo     未安装, 正在安装 pyinstaller ...
    python -m pip install pyinstaller || goto :fail
)

echo.
echo [2/3] PyInstaller 打包中 (onefile, 含 58MB 内核, 需要一两分钟) ...
python -m PyInstaller --noconfirm --clean "ant-py.spec"
if errorlevel 1 goto :fail

echo.
echo [3/3] 验证产物 ...
if not exist "dist\ant-py.exe" goto :fail

for %%A in ("dist\ant-py.exe") do echo     dist\ant-py.exe  %%~zA bytes

echo.
echo ============================================
echo   打包完成:  dist\ant-py.exe
echo   使用: 双击直接开（系统代理模式）。
echo   TUN 模式: 右键 -^> 以管理员身份运行
echo ============================================
exit /b 0

:fail
echo.
echo [FAIL] 打包失败, 看上面的报错信息。
exit /b 1

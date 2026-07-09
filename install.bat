@echo off
REM 安装 Reasonix Computer Use 插件包
REM 本脚本将插件复制到 Reasonix 的插件目录并验证安装

echo ============================================
echo  Reasonix Computer Use 插件 - 安装程序
echo ============================================
setlocal

REM 检查 Python 安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python 未安装或不在 PATH 中。
    echo 请从 https://www.python.org/downloads/ 安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查 pip
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip 未安装。
    pause
    exit /b 1
)

REM 步骤 1：安装 Python 依赖
echo [1/3] 安装 Python 依赖...
pip install -e .
if %errorlevel% neq 0 (
    echo ERROR: 安装 Python 依赖失败。
    pause
    exit /b 1
)
echo 依赖安装成功。

REM 步骤 2：将插件复制到 Reasonix 插件目录
set REASONIX_DIR=%USERPROFILE%\.reasonix\plugins\computer-use
if not exist "%REASONIX_DIR%" mkdir "%REASONIX_DIR%"

echo [2/3] 复制插件文件到 %REASONIX_DIR%...
xcopy /E /I /Y "%~dp0reasonix_computer_use" "%REASONIX_DIR%\reasonix_computer_use\"
xcopy /Y "%~dp0pyproject.toml" "%REASONIX_DIR%\"
xcopy /Y "%~dp0README.md" "%REASONIX_DIR%\"
xcopy /E /I /Y "%~dp0skills" "%REASONIX_DIR%\skills\"
xcopy /E /I /Y "%~dp0hooks" "%REASONIX_DIR%\hooks\"
xcopy /E /I /Y "%~dp0memory" "%REASONIX_DIR%\memory\"
xcopy /E /I /Y "%~dp0tests" "%REASONIX_DIR%\tests\"

echo 插件文件已复制。

REM 步骤 3：验证安装
echo [3/3] 验证安装...
python -c "from reasonix_computer_use import tools; print('插件加载正常')"
if %errorlevel% neq 0 (
    echo ERROR: 插件验证失败。
    pause
    exit /b 1
)

echo.
echo ============================================
echo  安装完成！
echo ============================================
echo.
echo 插件安装到：%REASONIX_DIR%
echo.
echo 使用方法：
echo  1. 打开 Reasonix Desktop
echo  2. 进入 插件 ^> 安装插件
echo  3. 选择"本地目录"
echo  4. 浏览到：%REASONIX_DIR%
echo  5. 点击"安装插件"
echo.
echo 或者在 reasonix.toml 中添加：
echo.
echo [[plugins]]
echo name    = "computer-use"
echo command = "python"
echo args    = ["-m", "reasonix_computer_use.mcp_server"]
echo type    = "stdio"
echo.
echo 然后重启 Reasonix。
echo.
pause

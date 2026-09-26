import os
import sys
import shutil
import pathlib
import tarfile
import datetime
import subprocess
import urllib.request
from datetime import datetime


# =============== 🛠️ 工具函数 ===============
def run(cmd: str, cwd: str = None, capture: bool = False) -> subprocess.CompletedProcess:
    """执行 shell 命令的通用函数"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=True
        )
        return result
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"命令执行失败: {cmd}\n{e.stderr}")


# =============== ⚙️ 配置类 ===============
class Config:
    """项目配置"""
    BASE_DIR = pathlib.Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "output"
    RCLONE_REMOTE = "FEADXUS-Google-Drive"
    RCLONE_REMOTE_PATH = f"{RCLONE_REMOTE}:/X/"


# =============== 📦 第 6️⃣ 步：设置 rclone 配置 ===============
def setup_rclone_config() -> None:
    """从环境变量读取 rclone 配置并写入文件"""
    rclone_secret = os.getenv("RCLONE_SECRET_DATA")
    if not rclone_secret:
        raise RuntimeError("❌ RCLONE_SECRET_DATA 环境变量未设置")

    conf_dir = os.path.expanduser("~/.config/rclone")
    os.makedirs(conf_dir, exist_ok=True)

    conf_path = os.path.join(conf_dir, "rclone.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(rclone_secret)
    
    print(f"✅ rclone 配置已写入: {conf_path}")


# =============== 🔽 第 7️⃣ 步：下载网页 ===============
def download_page(url: str) -> pathlib.Path:
    """使用 wget 下载网页"""
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 从 URL 提取文件名，若失败则用 index.html
    filename = pathlib.Path(url).name or "index.html"
    local_path = Config.OUTPUT_DIR / filename

    print(f"🔽 下载: {url} → {local_path}")
    run(f"wget -q -O '{local_path}' '{url}'")
    
    if not local_path.exists():
        raise RuntimeError(f"❌ 下载失败: {url}")
    
    print(f"✅ 下载完成")
    return local_path


# =============== 📦 第 8️⃣ 步：压缩 + 加密 ===============
def compress_and_encrypt(work_dir, output_file):
    """压缩并用 age 加密文件夹"""
    age_public_key = os.getenv('AGE_PUBLIC_KEY', '').strip()
    
    if not age_public_key:
        raise RuntimeError("AGE_PUBLIC_KEY 环境变量未设置")
    
    output_file = os.path.join(work_dir, output_file)
    folder_to_compress = os.path.basename(CONFIG.OUTPUT_DIR)  # 通常是 'output'
    
    cmd = (
        f"tar -cJf - -C '{work_dir}' '{folder_to_compress}' | "
        f"age -r '{age_public_key}' > '{output_file}'"
    )
    
    print(f"📦 正在打包压缩并加密文件夹 [{folder_to_compress}] -> {os.path.basename(output_file)}...")
    
    # ✅ 关键：加上 cwd 参数，和旧脚本一致
    result = subprocess.run(cmd, shell=True, check=True, cwd=work_dir)
    
    print(f"🔒 压缩加密完成! 生成文件: {output_file}")
    return True



# =============== ☁️ 第 9️⃣ 步：上传到 Google Drive ===============
def upload_to_drive(local_file: pathlib.Path, remote_path: str = None) -> None:
    """使用 rclone 上传文件到 Google Drive"""
    if remote_path is None:
        remote_path = Config.RCLONE_REMOTE_PATH
    
    # 确保末尾有斜杠
    remote_path = remote_path.rstrip("/") + "/"
    
    # 检查 rclone 是否安装
    if not shutil.which("rclone"):
        raise RuntimeError("❌ 未检测到 rclone，请先安装")

    # 检查本地文件是否存在
    if not local_file.exists():
        raise RuntimeError(f"❌ 本地文件不存在: {local_file}")

    # 确保远程目录存在
    print(f"📁 检查远程目录: {remote_path}")
    run(f"rclone mkdir '{remote_path}'")

    # 上传文件
    upload_cmd = f"rclone copy '{local_file}' '{remote_path}' --retries 3 -v"
    print(f"☁️ 上传中: {local_file.name} → {remote_path}")
    result = run(upload_cmd, capture=True)
    
    if result.stdout:
        print(result.stdout)
    
    print(f"🎉 上传成功!")


# =============== 🔟 主流程 ===============
def main() -> None:
    """整个工作流程"""
    try:
        print("\n" + "="*50)
        print("开始执行下载-压缩-加密-上传流程")
        print("="*50 + "\n")

        # Step 1: 设置 rclone
        print("[1/4] 设置 rclone 配置...")
        setup_rclone_config()

        # Step 2: 下载网页
        print("\n[2/4] 下载网页...")
        download_page("https://www.google.com")

        # Step 3: 压缩 + 加密
        print("\n[3/4] 压缩并加密...")
        output_filename = f"feadxus-backup-{datetime.now().strftime('%Y-%m-%d')}.tar.xz.age"
        compress_and_encrypt(CONFIG.BASE_DIR, output_filename)
        encrypted_file = os.path.join(CONFIG.BASE_DIR, output_filename)

        # Step 4: 上传到 Google Drive
        print("\n[4/4] 上传到 Google Drive...")
        upload_to_drive(encrypted_file)

        print("\n" + "="*50)
        print("✅ 全流程完成！")
        print("="*50 + "\n")

    except Exception as e:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] ❌ 错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

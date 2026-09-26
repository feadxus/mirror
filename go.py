import os
import sys
import shutil
import pathlib
import tarfile
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
CONFIG = Config()

# 1️⃣. 安装 pip 依赖库
pip_packages = [
    "google-auth-oauthlib",
    "google-api-python-client"
]
print("--> 1. 正在安装 Python 依赖库...")
# ⚠️ 注意:不能使用 sys.executable,直接调用系统环境的 pip3
subprocess.check_call(["pip3", "install", *pip_packages])


# 2️⃣. 下载并安装特定版本的 age (v1.3.2)
age_version = "v1.3.2"
url = f"https://github.com/FiloSottile/age/releases/download/{age_version}/age-{age_version}-linux-amd64.tar.gz"
tar_path = "/tmp/age.tar.gz"
extract_dir = "/tmp/age_bin"

print(f"--> 2. 正在从 GitHub 下载 age {age_version}...")
urllib.request.urlretrieve(url, tar_path)

print("--> 正在解压并安装 age 应用文件...")
os.makedirs(extract_dir, exist_ok=True)
with tarfile.open(tar_path, "r:gz") as tar:
    tar.extractall(path=extract_dir)

# 3️⃣. 将解压出来的 age 和 age-keygen 文件复制/移动到 /usr/local/bin/
src_dir = os.path.join(extract_dir, "age")
subprocess.check_call(["sudo", "cp", f"{src_dir}/age", f"{src_dir}/age-keygen", "/usr/local/bin/"])
subprocess.check_call(["sudo", "chmod", "+x", "/usr/local/bin/age", "/usr/local/bin/age-keygen"])

# 4️⃣. 安装 skopeo wget 下载工具 monolith 下载工具
subprocess.run(
    "sudo apt-get update && sudo apt-get install -y skopeo wget curl",
    shell=True,
    executable="/bin/bash",
    check=True,
)

# 安装 Rust + monolith
print("📦 安装 monolith...")
subprocess.run(
    "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && "
    "source $HOME/.cargo/env && "
    "cargo install monolith",
    shell=True,
    executable="/bin/bash",
    check=True,
)

# 下载预编译的 monolith 二进制
print("📦 安装 monolith...")
subprocess.run(
    "curl -L https://github.com/Y2Z/monolith/releases/download/v2.10.1/monolith-gnu-linux-x86_64 -o /usr/local/bin/monolith && "
    "chmod +x /usr/local/bin/monolith",
    shell=True,
    executable="/bin/bash",
    check=True,
)

# 5️⃣. ☁️ 安装 Google Drive 工具 (rclone) 与 skopeo
subprocess.run(
    "curl -fsSL https://rclone.org/install.sh | sudo bash",
    shell=True,
    executable="/bin/bash",
    check=True,
)

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
# 然后继续原有的逻辑
def download_page(url: str) -> pathlib.Path:
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = Config.OUTPUT_DIR / "archived_page.html"
    print(f"🔽 下载完整页面: {url}")
    run(f"monolith -o '{output_file}' '{url}'")
    print(f"✅ 下载完成: {output_file}")
    return output_file


# =============== 📦 第 8️⃣ 步：压缩 + 加密 ===============
def compress_and_encrypt(work_dir, output_file):
    """压缩并用 age 加密文件夹"""
    age_public_key = os.getenv('AGE_PUBLIC_KEY', '').strip()
    
    if not age_public_key:
        raise RuntimeError("AGE_PUBLIC_KEY 环境变量未设置")
    
    output_file = pathlib.Path(work_dir) / output_file  # ✅ 使用 Path
    folder_to_compress = os.path.basename(CONFIG.OUTPUT_DIR)
    
    cmd = (
        f"tar -cJf - -C '{work_dir}' '{folder_to_compress}' | "
        f"age -r '{age_public_key}' > '{output_file}'"
    )
    
    print(f"📦 正在打包压缩并加密文件夹 [{folder_to_compress}] -> {output_file.name}...")
    
    result = subprocess.run(cmd, shell=True, check=True, cwd=work_dir)
    
    print(f"🔒 压缩加密完成! 生成文件: {output_file}")
    return output_file  # ✅ 返回 Path 对象


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
        encrypted_file = compress_and_encrypt(CONFIG.BASE_DIR, output_filename)

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

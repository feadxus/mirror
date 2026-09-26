import os
import sys
import shutil
import pathlib
import tarfile
import datetime
import subprocess
import urllib.request


# ------------------- 配置 -------------------
class Config:
    """项目根目录 & 各子目录"""
    BASE_DIR   = pathlib.Path(__file__).resolve().parent   # 脚本所在目录
    OUTPUT_DIR = BASE_DIR / "output"                       # 下载后放这里
    RCLONE_REMOTE = "FEADXUS-Google-Drive"                 # 令牌变量名
    RCLONE_REMOTE_PATH = f"{RCLONE_REMOTE}:/X/"            # 上传到网盘指定目录

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

# 5️⃣. ☁️ 安装 Google Drive 工具 (rclone) 与 skopeo
print("--> 安装系统工具和 rclone...")

subprocess.run(
    "sudo apt-get update && "
    "sudo apt-get install -y skopeo wget && "
    "sudo rm -f /usr/local/bin/rclone && "  # ← 强制删除旧的 rclone
    "curl -fsSL https://rclone.org/install.sh | sudo bash",
    shell=True,
    executable="/bin/bash",
    check=True,
)

print("✅ 所有工具安装完成")


# 6️⃣. ⚙️ 设置 rclone 配置
rclone_secret = os.getenv("RCLONE_SECRET_DATA")
if not rclone_secret:
    raise RuntimeError("RCLONE_SECRET_DATA 环境变量未设置")

conf_dir = os.path.expanduser("~/.config/rclone")
os.makedirs(conf_dir, exist_ok=True)

conf_path = os.path.join(conf_dir, "rclone.conf")
with open(conf_path, "w", encoding="utf-8") as f:
    f.write(rclone_secret)
print(f"rclone config written to {conf_path}")


# 7️⃣. 下载测试文件
def download_test_page(url: str = "https://www.google.com") -> pathlib.Path:
    """使用 wget 下载一个网页并放入 OUTPUT_DIR，返回本地文件路径"""
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = pathlib.Path(url).name or "index.html"
    local_path = Config.OUTPUT_DIR / filename

    print(f"🔽 正在用 wget 下载 [{url}] → {local_path}")
    # -q  静默模式； -O 指定输出文件
    run(f"wget -q -O '{local_path}' '{url}'")
    if not local_path.exists():
        raise RuntimeError("下载失败，文件未生成")
    print("✅ 下载完成")
    return local_path

# 8️⃣. 压缩‑加密步骤
class CompressAndEncryptStep:
    """步骤 3：把 OUTPUT_DIR 打包压缩后，用 age 公钥加密"""
    def __init__(self, age_public_key: str):
        self.age_public_key = age_public_key

    def execute(self) -> pathlib.Path:
        if not Config.OUTPUT_DIR.exists() or not any(Config.OUTPUT_DIR.iterdir()):
            raise RuntimeError("⚠️ 待压缩的文件夹不存在或为空")

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        output_name = f"feadxus-gmail-{date_str}.tar.xz.age"
        output_path = Config.BASE_DIR / output_name

        cmd = (
            f"tar -cJf - -C '{Config.BASE_DIR}' '{Config.OUTPUT_DIR.name}' | "
            f"age -r '{self.age_public_key}' > '{output_path}'"
        )
        print(f"📦 正在压缩 → 加密 → {output_path.name}")
        run(cmd, cwd=Config.BASE_DIR)
        if not output_path.exists():
            raise RuntimeError("压缩加密后文件未生成")
        print(f"🔒 加密文件生成: {output_path}")
        return output_path


# 9️⃣. 上传至 Google Drive
class UploadToGoogleDriveStep:
    """步骤 4：使用 rclone 把加密文件上传到 Google Drive"""
    def __init__(self, remote_path: str = Config.RCLONE_REMOTE_PATH):
        # 确保末尾有斜杠，rclone copy 需要目录形式
        self.remote_path = remote_path.rstrip("/") + "/"

    def execute(self, local_file: pathlib.Path) -> None:
        if not shutil.which("rclone"):
            raise RuntimeError("❌ 未检测到 rclone，先按前面的步骤安装它")

        if not local_file.exists():
            raise RuntimeError(f"❌ 本地文件不存在: {local_file}")

        # 1️⃣ 确保远程目录已经创建
        print(f"📁 确保远程目录存在 → {self.remote_path}")
        run(f"rclone mkdir '{self.remote_path}'")

        # 2️⃣ 带重试 + 详细日志的上传命令
        upload_cmd = (
            f"rclone copy '{local_file}' '{self.remote_path}' "
            f"--retries 3 -v"
        )
        print(f"☁️ 上传 {local_file.name} → {self.remote_path} ...")
        result = run(upload_cmd, capture=True)
        print(result.stdout)  # rclone 的进度日志
        print(f"🎉 上传成功！文件已在 Drive 的 {self.remote_path} 中")


# 🔟. 主流程(把所有步骤串起来)
def main() -> None:
    try:
        # 支持先从环境变量取公钥，若没有则使用默认值
        AGE_PUBLIC_KEY = os.getenv("AGE_PUBLIC_KEY", "age1pq1pp")

        workflow = TorBridgeWorkflow(service)
        workflow.add_step(SendTorRequestStep()) \
                .add_step(PollAndProcessTorReplyStep()) \
                .add_step(CompressAndEncryptStep(age_public_key=AGE_PUBLIC_KEY)) \
                .add_step(UploadToGoogleDriveStep(remote_path="FEADXUS-Google-Drive:/Gmail/"))

        # ───── 下面是我们新加的“下载‑压缩‑上传”验证步骤 ─────
        # ① 下载一个示例页面放进 OUTPUT_DIR
        download_test_page("https://example.com")

        # ② 压缩并加密（直接复用同一个对象，避免重复实例化）
        encrypted_path = CompressAndEncryptStep(AGE_PUBLIC_KEY).execute()

        # ③ 上传到 Google Drive
        UploadToGoogleDriveStep().execute(encrypted_path)

        print("\n=== 全流程结束，验证成功 🎉 ===")
    except Exception as e:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now_str}] ❌ 运行抛出未捕获异常: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

import os
import sys
import time
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
    BASE_DIR = pathlib.Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "output"
    RCLONE_REMOTE = "FEADXUS-Google-Drive"
    RCLONE_REMOTE_PATH = f"{RCLONE_REMOTE}:/X/"
CONFIG = Config()


# =============== 📦 安装类 ===============
# 安装 pip 依赖库
pip_packages = [
    "google-auth-oauthlib",
    "google-api-python-client"
]
# ⚠️ 注意:不能使用 sys.executable,直接调用系统环境的 pip3
subprocess.check_call(["pip3", "install", *pip_packages])


# 下载并安装特定版本的 age (v1.3.2)
age_version = "v1.3.2"
url = f"https://github.com/FiloSottile/age/releases/download/{age_version}/age-{age_version}-linux-amd64.tar.gz"
tar_path = "/tmp/age.tar.gz"
extract_dir = "/tmp/age_bin"
urllib.request.urlretrieve(url, tar_path)
os.makedirs(extract_dir, exist_ok=True)
with tarfile.open(tar_path, "r:gz") as tar:
    tar.extractall(path=extract_dir)
src_dir = os.path.join(extract_dir, "age")
subprocess.check_call(["sudo", "cp", f"{src_dir}/age", f"{src_dir}/age-keygen", "/usr/local/bin/"])
subprocess.check_call(["sudo", "chmod", "+x", "/usr/local/bin/age", "/usr/local/bin/age-keygen"])


# 安装 skopeo wget 工具
subprocess.run(
    "sudo apt-get update && sudo apt-get install -y skopeo wget curl",
    shell=True,
    executable="/bin/bash",
    check=True,
)


# 安装 Rust 工具
subprocess.run(
    "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && "
    "source $HOME/.cargo/env && "
    "cargo install monolith",
    shell=True,
    executable="/bin/bash",
    check=True,
)


# 安装 monolith 工具
subprocess.run(
    "curl -L https://github.com/Y2Z/monolith/releases/download/v2.10.1/monolith-gnu-linux-x86_64 -o /usr/local/bin/monolith && "
    "chmod +x /usr/local/bin/monolith",
    shell=True,
    executable="/bin/bash",
    check=True,
)

# 安装 Google Drive rclone 与 skopeo 工具
subprocess.run(
    "curl -fsSL https://rclone.org/install.sh | sudo bash",
    shell=True,
    executable="/bin/bash",
    check=True,
)

# 设置 rclone 配置
def setup_rclone_config() -> None:
    rclone_secret = os.getenv("RCLONE_SECRET_DATA")
    if not rclone_secret:
        raise RuntimeError("❌ RCLONE_SECRET_DATA 环境变量未设置")
    conf_dir = os.path.expanduser("~/.config/rclone")
    os.makedirs(conf_dir, exist_ok=True)
    conf_path = os.path.join(conf_dir, "rclone.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(rclone_secret)
    print(f"✅ rclone 配置已写入: {conf_path}")



# =============== 🐳 Docker + Localtunnel ===============

DOCKER_CONTAINER_NAME = "feadxus-test-server"
DOCKER_PORT = 8080
DOCKER_IMAGE = "nginx:alpine"
docker_process = None
localtunnel_process = None
localtunnel_log_file = None

def check_required_commands() -> None:
    required_commands = [
        "docker",
        "curl",
        "npx",
    ]
    missing = []
    for command in required_commands:
        if shutil.which(command) is None:
            missing.append(command)
    if missing:
        raise RuntimeError(
            f"❌ 缺少必要命令: {', '.join(missing)}"
        )

# 启动 Docker 容器,并等待服务就绪.
def start_docker_container() -> None:
    print("🐳 启动 Docker 容器...")
    # 防止上一次残留同名容器导致启动失败
    subprocess.run(
        [
            "docker",
            "rm",
            "-f",
            DOCKER_CONTAINER_NAME,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            DOCKER_CONTAINER_NAME,
            "-p",
            f"{DOCKER_PORT}:80",
            DOCKER_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for i in range(1, 31):
        result = subprocess.run(
            [
                "curl",
                "-fsS",
                f"http://127.0.0.1:{DOCKER_PORT}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            print("✅ Docker 服务已启动")
            return
        if i == 30:
            show_docker_logs()
            raise RuntimeError("❌ Docker 服务启动失败")
        time.sleep(2)
    raise RuntimeError("❌ Docker 服务启动超时")

def show_docker_logs() -> None:
    """
    输出 Docker 容器日志。
    """
    result = subprocess.run(
        [
            "docker",
            "logs",
            DOCKER_CONTAINER_NAME,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

# 启动 Localtunnel，读取并返回公网地址。
def start_localtunnel() -> str:
    global localtunnel_process
    global localtunnel_log_file
    print("🌐 启动 Localtunnel...")
    log_path = pathlib.Path("/tmp/feadxus-localtunnel.log")
    localtunnel_log_file = log_path
    if log_path.exists():
        log_path.unlink()
    log_file = open(log_path, "w", encoding="utf-8")
    subdomain = f"test-{os.getenv('GITHUB_RUN_ID', str(int(time.time())))}"
    localtunnel_process = subprocess.Popen(
        [
            "npx",
            "--yes",
            "localtunnel",
            "--port",
            str(DOCKER_PORT),
            "--subdomain",
            subdomain,
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    tunnel_url = ""
    for _ in range(30):
        if log_path.exists():
            content = log_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("your url is:"):
                    tunnel_url = line.split("your url is:", 1)[1].strip()
                    break
        if tunnel_url:
            break
        if localtunnel_process.poll() is not None:
            content = log_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            raise RuntimeError(
                f"❌ Localtunnel 启动失败:\n{content}"
            )
        time.sleep(2)
    log_file.close()
    if not tunnel_url:
        content = log_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
        raise RuntimeError(
            f"❌ 未获取到 Localtunnel 地址:\n{content}"
        )
    print(f"✅ 公网地址：{tunnel_url}")
    return tunnel_url.rstrip("/")

# 清理 Localtunnel 和 Docker 容器
def cleanup_services() -> None:
    global localtunnel_process
    print("🧹 清理服务...")
    if localtunnel_process is not None:
        if localtunnel_process.poll() is None:
            localtunnel_process.terminate()
            try:
                localtunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                localtunnel_process.kill()
    subprocess.run(
        [
            "docker",
            "rm",
            "-f",
            DOCKER_CONTAINER_NAME,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    print("✅ 清理完成")






# 下载网页
def download_page_with_curl(url: str) -> pathlib.Path:
    """
    只从传入的映射域名下载页面。
    不跟随任何 HTTP 重定向。
    """

    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = (
        Config.OUTPUT_DIR / f"archived_page_{timestamp}.html"
    )

    print(f"⬇️ 仅从映射地址下载: {url}")

    result = subprocess.run(
        [
            "curl",
            "-f",              # HTTP 4xx/5xx 视为失败
            "-sS",             # 静默进度，但显示错误
            "--retry", "5",
            "--retry-delay", "3",
            "--connect-timeout", "15",
            "--max-time", "120",

            # 不允许跟随 301/302/307/308
            # 注意：这里故意不写 -L 或 --location
            "-H", "Bypass-Tunnel-Reminder: true",
            "-A", "Mozilla/5.0",

            "-o", str(output_file),

            # 输出最终实际请求地址和 HTTP 状态码
            "-w",
            "\nHTTP_STATUS:%{http_code}\n"
            "EFFECTIVE_URL:%{url_effective}\n",

            url,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if output_file.exists():
            output_file.unlink()

        raise RuntimeError(
            f"❌ 映射域名下载失败:\n"
            f"请求地址: {url}\n"
            f"{result.stderr.strip()}"
        )

    print(result.stdout.strip())

    # 解析 HTTP 状态码
    status_code = None

    for line in result.stdout.splitlines():
        if line.startswith("HTTP_STATUS:"):
            status_code = int(line.split(":", 1)[1])

    if status_code is None:
        if output_file.exists():
            output_file.unlink()
        raise RuntimeError("❌ 无法确认 HTTP 状态码")

    # 只接受 2xx，拒绝所有重定向
    if not 200 <= status_code < 300:
        if output_file.exists():
            output_file.unlink()

        raise RuntimeError(
            f"❌ 映射域名没有直接返回页面\n"
            f"请求地址: {url}\n"
            f"HTTP 状态码: {status_code}\n"
            f"程序未跟随重定向"
        )

    if not output_file.exists() or output_file.stat().st_size == 0:
        raise RuntimeError("❌ 下载结果为空")

    print(f"✅ 页面已从映射域名直接下载: {output_file}")

    return output_file



# 🔐 加密压缩下载的页面
def compress_and_encrypt(work_dir, output_file):
    age_public_key = os.getenv('AGE_PUBLIC_KEY', '').strip()
    if not age_public_key:
        raise RuntimeError("AGE_PUBLIC_KEY 环境变量未设置")
    output_file = pathlib.Path(work_dir) / output_file
    folder_to_compress = os.path.basename(CONFIG.OUTPUT_DIR)
    cmd = (
        f"tar -cJf - -C '{work_dir}' '{folder_to_compress}' | "
        f"age -r '{age_public_key}' > '{output_file}'"
    )
    result = subprocess.run(cmd, shell=True, check=True, cwd=work_dir)
    print(f"🔒 压缩加密完成! 生成文件: {output_file}")
    return output_file

# 保存至 Google Drive 网盘
def upload_to_drive(local_file: pathlib.Path, remote_path: str = None) -> None:
    if remote_path is None:
        remote_path = Config.RCLONE_REMOTE_PATH
    remote_path = remote_path.rstrip("/") + "/"
    if not shutil.which("rclone"):
        raise RuntimeError("❌ 未检测到 rclone,请先安装")
    if not local_file.exists():
        raise RuntimeError(f"❌ 本地文件不存在: {local_file}")
    print(f"📁 检查远程目录: {remote_path}")
    run(f"rclone mkdir '{remote_path}'")
    upload_cmd = f"rclone copy '{local_file}' '{remote_path}' --retries 3 -v"
    print(f"☁️ 上传中: {local_file.name} → {remote_path}")
    result = run(upload_cmd, capture=True)
    if result.stdout:
        print(result.stdout)
    print(f"🎉 上传成功!")


# 执行每个模块
def main() -> None:
    tunnel_url = None

    try:
        print("\n" + "="*50)
        print("开始执行下载-压缩-加密-上传流程")
        print("="*50 + "\n")

        # 设置 rclone
        print("[1/4] 设置 rclone 配置...")
        setup_rclone_config()

        check_required_commands()
        # 启动 Docker
        start_docker_container()

        # 启动 Localtunnel
        tunnel_url = start_localtunnel()

        # 下载网页
        print("\n[2/4] 下载网页...")
        download_page_with_curl(tunnel_url)

        # 压缩 + 加密
        print("\n[3/4] 压缩并加密...")
        output_filename = f"feadxus-backup-{datetime.now().strftime('%Y-%m-%d')}.tar.xz.age"
        encrypted_file = compress_and_encrypt(CONFIG.BASE_DIR, output_filename)

        # 上传到 Google Drive
        print("\n[4/4] 上传到 Google Drive...")
        upload_to_drive(encrypted_file)

        print("\n" + "="*50)
        print("✅ 全流程完成!")
        print("="*50 + "\n")

    except Exception as e:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] ❌ 错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

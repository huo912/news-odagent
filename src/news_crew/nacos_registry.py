"""
Nacos 服务注册（可选）

进程启动时把本服务注册到 Nacos 注册中心，使其出现在 Nacos 控制台的
"服务管理 -> 服务列表"中；进程退出时自动注销。

环境变量（未配置 NACOS_SERVER_ADDR 时整个功能静默跳过，不影响运行）：
    NACOS_SERVER_ADDR    Nacos 地址，如 127.0.0.1:8848（与配置中心共用）
    NACOS_NAMESPACE      命名空间 ID，留空为 public
    NACOS_USERNAME       鉴权用户名（可选）
    NACOS_PASSWORD       鉴权密码（可选）
    NACOS_SERVICE_NAME   注册的服务名，默认 news-odagent
    NACOS_SERVICE_GROUP  服务分组，默认 DEFAULT_GROUP
    NACOS_SERVICE_IP     注册 IP，默认自动探测本机出口 IP
    NACOS_SERVICE_PORT   注册端口，默认 8000（批处理任务无监听端口，
                         此端口仅作实例标识，可在控制台查看）

实现说明：
    nacos-sdk-python >= 3.x 为全异步 API，注册在独立守护线程的事件循环中
    完成。临时实例（ephemeral=true）由 SDK 的 gRPC 长连接维持心跳，
    线程常驻直到进程退出；退出时通过 atexit 显式注销实例并关闭客户端。
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import socket
import threading

logger = logging.getLogger(__name__)

ENV_SERVER_ADDR = "NACOS_SERVER_ADDR"
ENV_NAMESPACE = "NACOS_NAMESPACE"
ENV_USERNAME = "NACOS_USERNAME"
ENV_PASSWORD = "NACOS_PASSWORD"
ENV_SERVICE_NAME = "NACOS_SERVICE_NAME"
ENV_SERVICE_GROUP = "NACOS_SERVICE_GROUP"
ENV_SERVICE_IP = "NACOS_SERVICE_IP"
ENV_SERVICE_PORT = "NACOS_SERVICE_PORT"
ENV_TIMEOUT_MS = "NACOS_TIMEOUT_MS"

DEFAULT_SERVICE_NAME = "news-odagent"
DEFAULT_GROUP = "DEFAULT_GROUP"
DEFAULT_PORT = 8000
DEFAULT_TIMEOUT_MS = 10_000

_started = False
_lock = threading.Lock()
_stop = threading.Event()
_ready = threading.Event()
_registered = False


def _local_ip(server_addr: str) -> str:
    """自动探测本机对 Nacos 服务可达的出口 IP（不真正发包）。"""
    host = server_addr.split(",")[0].strip().split(":")[0]
    port_s = server_addr.split(",")[0].strip().split(":")
    port = int(port_s[1]) if len(port_s) > 1 else 8848
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _build_client_config():
    from v2.nacos import ClientConfigBuilder  # type: ignore[import-not-found]

    namespace = os.getenv(ENV_NAMESPACE, "").strip()
    username = os.getenv(ENV_USERNAME, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    timeout_ms = int(os.getenv(ENV_TIMEOUT_MS, DEFAULT_TIMEOUT_MS))

    builder = ClientConfigBuilder().server_address(
        os.getenv(ENV_SERVER_ADDR, "").strip()
    ).timeout_ms(timeout_ms)
    if namespace:
        builder = builder.namespace_id(namespace)
    if username:
        builder = builder.username(username).password(password)
    return builder.build()


def _run_worker() -> None:
    """后台线程：注册实例 -> 常驻维持 -> 退出时注销。"""
    global _registered
    from v2.nacos import (  # type: ignore[import-not-found]
        DeregisterInstanceParam,
        NacosNamingService,
        RegisterInstanceParam,
    )

    service_name = os.getenv(ENV_SERVICE_NAME, DEFAULT_SERVICE_NAME).strip()
    group = os.getenv(ENV_SERVICE_GROUP, DEFAULT_GROUP).strip()
    port = int(os.getenv(ENV_SERVICE_PORT, DEFAULT_PORT))
    ip = os.getenv(ENV_SERVICE_IP, "").strip() or _local_ip(
        os.getenv(ENV_SERVER_ADDR, "")
    )

    async def _main() -> None:
        global _registered
        naming = await NacosNamingService.create_naming_service(
            _build_client_config()
        )
        try:
            ok = await naming.register_instance(
                RegisterInstanceParam(
                    service_name=service_name,
                    group_name=group,
                    ip=ip,
                    port=port,
                    ephemeral=True,
                    metadata={"app": "news-odagent", "framework": "crewai"},
                )
            )
            _registered = bool(ok)
            _ready.set()
            if ok:
                logger.info(
                    "Nacos 服务注册成功: %s/%s (%s:%s)",
                    group, service_name, ip, port,
                )
            else:
                logger.warning("Nacos 服务注册返回失败: %s", service_name)
                return
            # 常驻：保持 gRPC 连接存活（临时实例靠连接维持心跳），
            # 直到收到停止信号
            while not _stop.is_set():
                await asyncio.sleep(1)
        finally:
            try:
                if _registered:
                    await naming.deregister_instance(
                        DeregisterInstanceParam(
                            service_name=service_name,
                            group_name=group,
                            ip=ip,
                            port=port,
                            ephemeral=True,
                        )
                    )
                    logger.info("Nacos 服务已注销: %s", service_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("Nacos 服务注销失败: %s", e)
            await naming.shutdown()

    try:
        asyncio.run(_main())
    except Exception as e:  # noqa: BLE001 - 后台线程异常不向主线程传播
        logger.warning("Nacos 服务注册 worker 退出，跳过服务注册: %s", e)
    finally:
        _ready.set()


def register_service() -> None:
    """
    注册本服务到 Nacos（幂等，非阻塞）。

    未配置 NACOS_SERVER_ADDR 或未安装 nacos-sdk-python 时静默跳过。
    进程退出时自动注销（atexit）。
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True

    if not os.getenv(ENV_SERVER_ADDR, "").strip():
        logger.debug("未配置 %s，跳过服务注册", ENV_SERVER_ADDR)
        return
    try:
        import v2.nacos  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        logger.warning(
            "已配置 NACOS_SERVER_ADDR 但未安装 nacos-sdk-python，"
            "跳过服务注册。安装: pip install -e \".[nacos]\""
        )
        return

    t = threading.Thread(target=_run_worker, name="nacos-registry-worker", daemon=True)
    t.start()

    def _shutdown() -> None:
        _stop.set()
        t.join(timeout=10)

    atexit.register(_shutdown)


def wait_registered(timeout: float = 15) -> bool:
    """等待注册完成（用于测试/诊断），返回是否注册成功。"""
    _ready.wait(timeout=timeout)
    return _registered

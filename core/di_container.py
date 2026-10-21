"""
依赖注入容器 - 解决全局单例问题，提供更好的依赖管理
"""

from typing import Dict, Any, Callable, Type, Optional, Union
from enum import Enum
import threading
import asyncio
from utils.logger_loguru import get_logger

import typing
from typing import get_origin


def _is_builtin_or_generic_type(annotation) -> bool:
    """判断类型注解是否是内置类型或泛型容器类型（不需要从 DI 容器解析）。"""
    # 1. 真正的内置类型：int, str, float, bool, bytes, NoneType 等
    if hasattr(annotation, '__module__') and annotation.__module__ == 'builtins':
        return True

    # 2. typing 模块的泛型类型：List, Dict, Optional, Union, Tuple 等
    origin = get_origin(annotation)
    if origin is not None:
        # 如果原始类型是内置类型，说明这是内置类型的泛型化，如 List[int]、dict[str, int]
        if hasattr(origin, '__module__') and origin.__module__ == 'builtins':
            return True
        # 如果原始类型本身来自 typing 模块，如 Union、Optional
        if hasattr(origin, '__module__') and origin.__module__ == 'typing':
            return True

    # 3. NoneType
    if annotation is type(None):
        return True

    return False

class ServiceLifetime(Enum):
    """服务生命周期"""
    SINGLETON = "singleton"     # 单例
    TRANSIENT = "transient"     # 每次创建新实例
    SCOPED = "scoped"           # 作用域内单例

class ServiceDescriptor:
    """服务描述符"""
    def __init__(self, service_type: Type,
                 implementation_type: Optional[Type] = None,
                 factory: Optional[Callable] = None,
                 instance: Optional[Any] = None,
                 lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT):
        self.service_type = service_type
        self.implementation_type = implementation_type
        self.factory = factory
        self.instance = instance
        self.lifetime = lifetime

class Scope:
    """作用域缓存"""
    def __init__(self, container, parent: Optional['Scope'] = None):
        self._container = container
        self._parent = parent
        self._scoped_instances = {}     # 每个 Scope 拥有自己独立的缓存

    def get(self, service_type: Union[str, Type]):
        if isinstance(service_type, str):
            key = service_type
        else:
            key = service_type.__name__

        if key in self._scoped_instances:
            return self._scoped_instances[key]
        if self._parent:
            return self._parent.get(key)
        return None

    def set(self, service_type, instance):
        if isinstance(service_type, str):
            key = service_type
        else:
            key = service_type.__name__
        self._scoped_instances[key] = instance

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._container.dispose_scope()      # 退出 with 时自动弹出作用域
        return False                         # 不吞异常，让异常继续向上抛

class DIContainer:
    """依赖注入容器 - 支持多种服务生命周期"""

    def __init__(self):
        self._services: Dict[str, ServiceDescriptor] = {}
        self._singletons: Dict[str, Any] = {}           # 单例
        self._scoped_instances: Dict[str, Any] = {}     # 作用域内单例
        self._scope_stack = []                          # 新增：作用域栈，初始自带一个全局根作用域
        self._lock = threading.RLock()
        self.logger = get_logger("DIContainer")

    def register_singleton(self, service_type: Type, instance: Any = None, factory: Callable = None, implementation_type: Type = None):
        with self._lock:
            key = service_type.__name__
            if key in self._services:
                self.logger.warning(f"服务 {key} 已注册")
                raise ValueError(f"服务 {key} 已注册")

        """注册单例服务"""
        if instance is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                instance=instance,
                lifetime=ServiceLifetime.SINGLETON
            )
        elif factory is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                factory=factory,
                lifetime=ServiceLifetime.SINGLETON
            )
        elif implementation_type is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                implementation_type=implementation_type,
                lifetime=ServiceLifetime.SINGLETON
            )
        else:
            raise ValueError("必须提供 instance、factory 或 implementation_type 中的一个")

        with self._lock:
            key = service_type.__name__
            self._services[key] = descriptor
            self.logger.debug(f"注册单例服务: {key}")

        return self

    def register_transient(self, service_type: Type, implementation_type: Type = None, factory: Callable = None):
        """注册瞬态服务（每次都创建新实例）"""
        with self._lock:
            key = service_type.__name__
            if key in self._services:
                self.logger.warning(f"服务 {key} 已注册")
                raise ValueError(f"服务 {key} 已注册")

        if implementation_type is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                implementation_type=implementation_type,
                lifetime=ServiceLifetime.TRANSIENT
            )
        elif factory is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                factory=factory,
                lifetime=ServiceLifetime.TRANSIENT
            )
        else:
            raise ValueError("必须提供 implementation_type 或 factory")

        with self._lock:
            key = service_type.__name__
            self._services[key] = descriptor
            self.logger.debug(f"注册瞬态服务: {key}")

        return self

    def register_scoped(self, service_type: Type, implementation_type: Type = None,
                       factory: Callable = None):
        """注册作用域服务（在同一个作用域内是单例）"""
        with self._lock:
            key = service_type.__name__
            if key in self._services:
                self.logger.warning(f"服务 {key} 已注册")
                raise ValueError(f"服务 {key} 已注册")

        if implementation_type is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                implementation_type=implementation_type,
                lifetime=ServiceLifetime.SCOPED
            )
        elif factory is not None:
            descriptor = ServiceDescriptor(
                service_type=service_type,
                factory=factory,
                lifetime=ServiceLifetime.SCOPED
            )
        else:
            raise ValueError("必须提供 implementation_type 或 factory")

        with self._lock:
            key = service_type.__name__
            self._services[key] = descriptor
            self.logger.debug(f"注册作用域服务: {key}")

        return self

    def get(self, service_type: Union[str, Type], **kwargs) -> Any:
        if isinstance(service_type, str):
            key = service_type
        else:
            key = service_type.__name__

        with self._lock:
            if key not in self._services:
                raise ValueError(f"服务 {key} 未注册")
            descriptor = self._services[key]

        # 处理单例（双重检查锁定，避免在锁内执行耗时创建）
        if descriptor.lifetime == ServiceLifetime.SINGLETON:
            if key in self._singletons:
                return self._singletons[key]

            with self._lock:
                if key in self._singletons:
                    return self._singletons[key]

                if descriptor.instance is not None:
                    instance = descriptor.instance
                elif descriptor.factory is not None:
                    instance = descriptor.factory()
                elif descriptor.implementation_type is not None:
                    instance = self._create_instance(descriptor.implementation_type, **kwargs)
                else:
                    raise ValueError(f"无法创建单例服务 {key}")

                self._singletons[key] = instance
                return instance

        # 处理作用域
        elif descriptor.lifetime == ServiceLifetime.SCOPED:
            # 找实例
            instance = self._scope_stack[-1].get(key)
            if instance:
                return instance

            with self._lock:
                instance = self._scope_stack[-1].get(key)
                if instance:
                    return instance

                if descriptor.factory is not None:
                    instance = descriptor.factory()
                elif descriptor.implementation_type is not None:
                    instance = self._create_instance(descriptor.implementation_type)
                else:
                    raise ValueError(f"无法创建作用域服务 {key}")

                self._scope_stack[-1].set(key, instance)
                return instance

        # 处理瞬态
        elif descriptor.lifetime == ServiceLifetime.TRANSIENT:
            if descriptor.factory is not None:
                return descriptor.factory()
            elif descriptor.implementation_type is not None:
                return self._create_instance(descriptor.implementation_type, **kwargs)
            else:
                raise ValueError(f"无法创建瞬态服务 {key}")
        else:
            return None

    async def get_async(self, service_type: Type) -> Any:
        """异步获取服务实例"""
        key = service_type.__name__

        with self._lock:
            if key not in self._services:
                raise ValueError(f"服务 {service_type.__name__} 未注册")
            descriptor = self._services[key]

        # 处理单例（双重检查锁定，避免在锁内执行耗时创建）
        if descriptor.lifetime == ServiceLifetime.SINGLETON:
            if key in self._singletons:
                return self._singletons[key]

            with self._lock:
                if key in self._singletons:
                    return self._singletons[key]

                if descriptor.instance is not None:
                    instance = descriptor.instance
                elif descriptor.factory is not None and asyncio.iscoroutinefunction(descriptor.factory):
                    instance = await descriptor.factory()
                elif descriptor.factory is not None:
                    instance = descriptor.factory()
                elif descriptor.implementation_type is not None:
                    instance = await self._create_instance_async(descriptor.implementation_type)
                else:
                    raise ValueError(f"无法创建单例服务 {service_type.__name__}")

                self._singletons[key] = instance
                return instance

        # 处理作用域
        elif descriptor.lifetime == ServiceLifetime.SCOPED:
            if key in self._scoped_instances:
                return self._scoped_instances[key]

            with self._lock:
                if key in self._scoped_instances:
                    return self._scoped_instances[key]

                if descriptor.factory is not None and asyncio.iscoroutinefunction(descriptor.factory):
                    instance = await descriptor.factory()
                elif descriptor.factory is not None:
                    instance = descriptor.factory()
                elif descriptor.implementation_type is not None:
                    instance = await self._create_instance_async(descriptor.implementation_type)
                else:
                    raise ValueError(f"无法创建作用域服务 {service_type.__name__}")

                self._scoped_instances[key] = instance
                return instance

        # 处理瞬态
        elif descriptor.lifetime == ServiceLifetime.TRANSIENT:
            if descriptor.factory is not None and asyncio.iscoroutinefunction(descriptor.factory):
                return await descriptor.factory()
            elif descriptor.factory is not None:
                return descriptor.factory()
            elif descriptor.implementation_type is not None:
                return await self._create_instance_async(descriptor.implementation_type)
            else:
                raise ValueError(f"无法创建瞬态服务 {service_type.__name__}")

    def _create_instance(self, implementation_type: Type, **kwargs) -> Any:
        """创建实例（自动注入依赖）"""
        try:
            # 检查是否有构造函数参数需要注入
            import inspect
            signature = inspect.signature(implementation_type.__init__)

            if len(signature.parameters) <= 1:  # 只有self参数
                return implementation_type()

            # 需要依赖注入
            for param_name, param in signature.parameters.items():
                if param_name == 'self':
                    continue

                if param_name in kwargs:
                    continue

                if param.annotation != inspect.Parameter.empty:
                    dependency_type = param.annotation

                    # 内置类型不走容器
                    if _is_builtin_or_generic_type(dependency_type):
                        if param.default is not inspect.Parameter.empty:
                            kwargs[param_name] = param.default
                        continue

                    kwargs[param_name] = self.get(dependency_type)
                elif param.default is not inspect.Parameter.empty:
                    kwargs[param_name] = param.default

            return implementation_type(**kwargs)
        except Exception as e:
            self.logger.error(
                f"创建实例失败 {implementation_type.__name__}: "
                f"error_type={type(e).__name__}:{e}"
            )
            raise RuntimeError(f"Failed to create instance of {implementation_type.__name__}") from e

    async def _create_instance_async(self, implementation_type: Type) -> Any:
        """异步创建实例（自动注入依赖）"""
        try:
            # 检查是否有构造函数参数需要注入
            import inspect
            signature = inspect.signature(implementation_type.__init__)

            if len(signature.parameters) <= 1:  # 只有self参数
                return implementation_type()

            # 需要依赖注入
            kwargs = {}
            for param_name, param in signature.parameters.items():
                if param_name == 'self':
                    continue

                if param.annotation != inspect.Parameter.empty:
                    dependency_type = param.annotation
                    kwargs[param_name] = await self.get_async(dependency_type)

            return implementation_type(**kwargs)
        except Exception as e:
            self.logger.error(
                f"异步创建实例失败 {implementation_type.__name__}: "
                f"error_type={type(e).__name__}"
            )
            raise

    def create_scope(self, parent=None):
        """创建并进入新的子作用域（如处理每个 HTTP 请求）"""
        scope = Scope(self, parent)
        self._scope_stack.append(scope)
        return scope

    def dispose_scope(self):
        """销毁当前作用域（清理 Scoped 实例）"""
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()

    def clear_scoped(self):
        """保留原有方法：全局清除所有非根作用域的 Scoped 实例"""
        while len(self._scope_stack) > 1:
            self._scope_stack.pop()

    # def clear_scoped(self):
    #     """清除作用域实例"""
    #     with self._lock:
    #         self._scoped_instances.clear()
    #         self.logger.debug("已清除所有作用域实例")

    def is_registered(self, service_type: Type) -> bool:
        """检查服务是否已注册"""
        key = service_type.__name__
        return key in self._services

    def get_registered_services(self) -> Dict[str, str]:
        """获取已注册的服务列表"""
        with self._lock:
            return {
                key: f"{descriptor.service_type.__name__} ({descriptor.lifetime.value})"
                for key, descriptor in self._services.items()
            }

    def dispose(self):
        """释放容器资源"""
        with self._lock:
            # 调用单例实例的dispose方法（如果有）
            for instance in self._singletons.values():
                if hasattr(instance, 'dispose') and callable(getattr(instance, 'dispose')):
                    try:
                        instance.dispose()
                    except Exception as e:
                        self.logger.error(
                            f"释放实例失败: error_type={type(e).__name__}"
                        )

            # 清除所有实例
            self._singletons.clear()
            self._scoped_instances.clear()
            self.logger.info("依赖注入容器已释放")


# 全局容器实例
container = DIContainer()


def configure_standard_services(config_instance: Any = None) -> 'DIContainer':
    """
    按依赖顺序注册核心标准服务到 DI 容器

    目前仅注册需要 DI 统一管理的服务：
    1. ConnectionStatusManager — 连接状态管理（跨 PDDChannel 实例共享）
    2. DatabaseManager — 数据库管理器

    其他服务（QueueManager、MessageConsumerManager）
    已通过模块级单例直接使用，无需重复注册。

    必须在其他模块导入前调用（通常在 app.py 中），
    以确保服务在需要时可从容器获取。
    """
    # 提前导入，避免 Python 局部变量作用域问题
    from core.connection_status import ConnectionStatusManager
    from database.db_manager import DatabaseManager

    # 1. ConnectionStatusManager（最独立，先注册）
    if not container.is_registered(ConnectionStatusManager):
        container.register_singleton(
            ConnectionStatusManager,
            factory=lambda: ConnectionStatusManager()
        )

    # 2. DatabaseManager
    if not container.is_registered(DatabaseManager):
        db_path = "./temp/channel_shop.db"
        if config_instance is not None:
            db_path = config_instance.get("db_path", db_path) or db_path
        container.register_singleton(
            DatabaseManager,
            factory=lambda: DatabaseManager(db_path=db_path)
        )

    # 3. KnowledgeService（知识库服务）
    from database.knowledge_service import KnowledgeService
    if not container.is_registered(KnowledgeService):
        container.register_singleton(
            KnowledgeService,
            factory=lambda: KnowledgeService()
        )

    # 4. Agent 均为 transient。运行中的账号各自拥有独立的 Agent、
    # LLM client 和 SessionManager，异步状态不会跨账号泄漏。

    # 4.1 AgenticRAGAgent（带自反思的 RAG Agent），同为 transient，
    # try:
    #     from Agent.AgenticRAGAgent import AgenticRAGAgent
    # except ImportError as exc:
    #     container.logger.warning(f"AgenticRAGAgent 依赖未就绪，跳过注册: {exc}。")
    # else:
    #     if not container.is_registered(AgenticRAGAgent):
    #         container.register_transient(AgenticRAGAgent,factory=lambda: AgenticRAGAgent())

    # 注：QueueManager、MessageConsumerManager 已在各自模块中定义了模块级单例实例
    # （如 Message/core/queue.py 中的 queue_manager），代码中通过
    # `from Message import queue_manager` 直接使用，无需重复注册到 DI 容器，
    # 保持 DI 容器仅管理需要跨模块共享的服务（如 ConnectionStatusManager）。

    container.logger.info(f"标准服务配置完成，已注册: {list(container.get_registered_services().keys())}")
    return container

# 便捷装饰器
def injectable(service_type: Type = None, lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT):
    """可注入装饰器"""
    def decorator(cls):
        nonlocal service_type
        if service_type is None:
            service_type = cls

        if lifetime == ServiceLifetime.SINGLETON:
            container.register_singleton(service_type, implementation_type=cls)
        elif lifetime == ServiceLifetime.TRANSIENT:
            container.register_transient(service_type, implementation_type=cls)
        elif lifetime == ServiceLifetime.SCOPED:
            container.register_scoped(service_type, implementation_type=cls)

        return cls
    return decorator

if __name__ == '__main__':
    from typing import List, Dict, Optional, Union
    from langchain_core.documents import Document
    import inspect

    # 测试各种类型注解
    tests = [
        Optional[List],  # typing, origin=list → True
        List[Document],  # typing, origin=list → True
        int,  # builtins → True
        List[int],  # typing, origin=list → True
        DIContainer,  # 自定义类 → False
    ]

    for t in tests:
        print(f"{t!r:30} → {_is_builtin_or_generic_type(t)}")
    # 定义两个底层依赖
    class DatabaseConnection:
        def __init__(self, host: str, port: int):
            self.host = host
            self.port = port
            print(f"[DatabaseConnection] 已连接 {host}:{port}")


    class CacheService:
        def __init__(self, ttl: int = 300):
            self.ttl = ttl
            print(f"[CacheService] 已创建，TTL={ttl}s")


    # 定义一个业务服务，它依赖上面两个类
    @injectable()
    class UserService:
        def __init__(self, db: DatabaseConnection, cache: CacheService):
            self.db = db
            self.cache = cache
            print("[UserService] 已创建，依赖已自动注入")

        def get_user(self, user_id: int):
            return {"id": user_id, "name": "Alice"}


    container.register_singleton(DatabaseConnection, factory=lambda: DatabaseConnection("localhost", 5432))
    container.register_singleton(CacheService, factory=lambda: CacheService())
    # # 注册业务服务 → 用 implementation_type，容器自动搞定 db 和 cache 的注入
    # container.register_singleton(UserService, implementation_type=UserService)
    # 获取服务 → 所有依赖一层层自动解析
    user_service = container.get("UserService")
    # 验证依赖确实被注入了
    print(user_service.db.host)  # 输出: localhost
    print(user_service.cache.ttl)  # 输出: 300
    user_service.get_user(1)  # 正常使用
    pass
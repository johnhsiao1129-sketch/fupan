# -*- coding: utf-8 -*-
"""
API 调用频率限制器
参考项目: daily_stock_analysis/data_provider/

功能：
1. 速率限制器 - 控制API调用频率，避免被限流
2. 熔断器 - 连续失败后自动冷却，避免反复请求失败接口
3. 指数退避重试 - 请求失败后自动重试，间隔时间指数增长

使用方式：
    from src.api_rate_limiter import get_api_rate_limiter
    
    # 方式1: 在API调用前手动调用
    limiter = get_api_rate_limiter()
    limiter.wait_before_request()
    df = ak.stock_zh_a_spot_em()
    
    # 方式2: 使用装饰器（自动重试）
    @limiter.with_retry(max_retries=3)
    def call_api():
        return ak.stock_zh_a_spot_em()
"""

import logging
import random
import time
import threading
from dataclasses import dataclass
from typing import Dict, Any, Optional, Callable, TYPE_CHECKING
from functools import wraps

if TYPE_CHECKING:
    from tenacity import (  # type: ignore[import-not-found]
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
    )

# tenacity 可选依赖：缺失时退化为无重试版（仅打 log）
try:
    from tenacity import (  # type: ignore[assignment]
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
    )
    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False
    logging.getLogger(__name__).warning("tenacity 未安装, 速率限制器失去自动重试能力 (本地兜底走单次)")

    # 提供 no-op 占位装饰器, 避免调用方崩溃
    def retry(*_args, **_kwargs):
        def decorator(func):
            return func
        return decorator

    def stop_after_attempt(*_args, **_kwargs):
        return None

    def wait_exponential(*_args, **_kwargs):
        return None

    def retry_if_exception_type(*_args, **_kwargs):
        return None

    def before_sleep_log(*_args, **_kwargs):
        return None

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    熔断器 - 管理API的熔断/冷却状态
    
    策略：
    - 连续失败 N 次后进入熔断状态
    - 熔断期间跳过该API
    - 冷却时间后自动恢复半开状态
    - 半开状态下单次成功则完全恢复，失败则继续熔断
    
    状态机：
    CLOSED（正常）--失败N次--> OPEN（熔断）--冷却时间到--> HALF_OPEN（半开）
    HALF_OPEN --成功--> CLOSED
    HALF_OPEN --失败--> OPEN
    """
    
    # 状态常量
    CLOSED = "closed"          # 正常状态
    OPEN = "open"              # 熔断状态（不可用）
    HALF_OPEN = "half_open"    # 半开状态（试探性请求）
    
    def __init__(
        self,
        failure_threshold: int = 3,       # 连续失败次数阈值
        cooldown_seconds: float = 300.0,  # 冷却时间（秒），默认5分钟
        half_open_max_calls: int = 1      # 半开状态最大尝试次数
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        
        # 各API状态 {api_name: {state, failures, last_failure_time, half_open_calls}}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def _get_state(self, api_name: str) -> Dict[str, Any]:
        """获取或初始化API状态"""
        if api_name not in self._states:
            self._states[api_name] = {
                'state': self.CLOSED,
                'failures': 0,
                'last_failure_time': 0.0,
                'half_open_calls': 0
            }
        return self._states[api_name]
    
    def is_available(self, api_name: str) -> bool:
        """
        检查API是否可用
        
        返回 True 表示可以尝试请求
        返回 False 表示应跳过该API
        """
        with self._lock:
            state = self._get_state(api_name)
            current_time = time.time()
            
            if state['state'] == self.CLOSED:
                return True
            
            if state['state'] == self.OPEN:
                # 检查冷却时间
                time_since_failure = current_time - state['last_failure_time']
                if time_since_failure >= self.cooldown_seconds:
                    # 冷却完成，进入半开状态
                    state['state'] = self.HALF_OPEN
                    state['half_open_calls'] = 0
                    logger.info(f"[熔断器] {api_name} 冷却完成，进入半开状态")
                    return True
                else:
                    remaining = self.cooldown_seconds - time_since_failure
                    logger.debug(f"[熔断器] {api_name} 处于熔断状态，剩余冷却时间: {remaining:.0f}s")
                    return False
            
            if state['state'] == self.HALF_OPEN:
                # 半开状态下限制请求次数
                if state['half_open_calls'] < self.half_open_max_calls:
                    return True
                return False
            
            return True
    
    def record_success(self, api_name: str) -> None:
        """记录成功请求"""
        with self._lock:
            state = self._get_state(api_name)
            
            if state['state'] == self.HALF_OPEN:
                # 半开状态下成功，完全恢复
                logger.info(f"[熔断器] {api_name} 半开状态请求成功，恢复正常")
            
            # 重置状态
            state['state'] = self.CLOSED
            state['failures'] = 0
            state['half_open_calls'] = 0
    
    def record_failure(self, api_name: str, error: Optional[str] = None) -> None:
        """记录失败请求"""
        with self._lock:
            state = self._get_state(api_name)
            current_time = time.time()
            
            state['failures'] += 1
            state['last_failure_time'] = current_time
            
            if state['state'] == self.HALF_OPEN:
                # 半开状态下失败，继续熔断
                state['state'] = self.OPEN
                state['half_open_calls'] = 0
                logger.warning(f"[熔断器] {api_name} 半开状态请求失败，继续熔断 {self.cooldown_seconds}s")
            elif state['failures'] >= self.failure_threshold:
                # 达到阈值，进入熔断
                state['state'] = self.OPEN
                logger.warning(f"[熔断器] {api_name} 连续失败 {state['failures']} 次，进入熔断状态 "
                              f"(冷却 {self.cooldown_seconds}s)")
                if error:
                    logger.warning(f"[熔断器] 最后错误: {error}")

    def reset(self, api_name: Optional[str] = None) -> None:
        """重置熔断器状态"""
        with self._lock:
            if api_name:
                if api_name in self._states:
                    del self._states[api_name]
                    logger.info(f"[熔断器] 已重置 API {api_name} 的状态")
            else:
                self._states.clear()
                logger.info(f"[熔断器] 已重置所有API的状态")


@dataclass
class RateLimitConfig:
    """速率限制配置"""
    min_interval: float = 1.0      # 最小间隔（秒）
    max_interval: float = 3.0      # 最大间隔（秒）
    batch_size: int = 10           # 每N次请求后休眠
    batch_sleep: float = 5.0       # 批量休眠时间（秒）
    enable_jitter: bool = True     # 是否启用随机抖动


class APIRateLimiter:
    """
    API 速率限制器
    
    功能：
    1. 每次请求前强制最小间隔 + 随机Jitter
    2. 每N次请求后休眠更长时间
    3. 集成熔断器，API连续失败后自动冷却
    4. 支持按API名称区分不同的限制策略
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            cooldown_seconds=300.0,
            half_open_max_calls=1
        )
        
        # 各API的请求记录 {api_name: {last_request_time, request_count}}
        self._request_records: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def _random_sleep(self, min_sec: float = 1.0, max_sec: float = 3.0) -> None:
        """
        随机休眠（Jitter）
        
        防封禁策略：模拟人类行为的随机延迟
        在请求之间加入不规则的等待时间
        """
        sleep_time = random.uniform(min_sec, max_sec)
        if self.config.enable_jitter:
            time.sleep(sleep_time)
    
    def _get_record(self, api_name: str) -> Dict[str, Any]:
        """获取或初始化API请求记录"""
        if api_name not in self._request_records:
            self._request_records[api_name] = {
                'last_request_time': 0.0,
                'request_count': 0
            }
        return self._request_records[api_name]
    
    def wait_before_request(self, api_name: str = "default") -> None:
        """
        在API调用前等待，确保速率限制

        Args:
            api_name: API名称，用于区分不同的API
        """
        # 检查熔断器状态
        if not self.circuit_breaker.is_available(api_name):
            logger.warning(f"[速率限制] API {api_name} 处于熔断状态，跳过请求")
            raise Exception(f"API {api_name} 处于熔断状态，不可用")

        with self._lock:
            record = self._get_record(api_name)
            current_time = time.time()

            # 检查距离上次请求的时间间隔
            elapsed = current_time - record['last_request_time']
            min_interval = self.config.min_interval

            # 计算需要休眠的时间
            additional_sleep = 0
            if record['last_request_time'] > 0 and elapsed < min_interval:
                additional_sleep = min_interval - elapsed
                logger.debug(f"[速率限制] API {api_name} 补充休眠 {additional_sleep:.2f}s")

            # 更新记录（在释放锁之前）
            record['last_request_time'] = time.time()
            record['request_count'] += 1

            # 检查是否需要批量休眠（保存到局部变量）
            need_batch_sleep = (record['request_count'] % self.config.batch_size == 0)
            request_count = record['request_count']

        # 在锁外执行休眠，避免死锁
        if additional_sleep > 0:
            time.sleep(additional_sleep)

        # 执行随机 jitter 休眠
        self._random_sleep(self.config.min_interval, self.config.max_interval)

        # 批量休眠
        if need_batch_sleep:
            logger.info(f"[速率限制] API {api_name} 已请求 {request_count} 次，休眠 {self.config.batch_sleep}s")
            time.sleep(self.config.batch_sleep)
    
    def record_success(self, api_name: str = "default") -> None:
        """记录API请求成功"""
        self.circuit_breaker.record_success(api_name)
    
    def record_failure(self, api_name: str = "default", error: Optional[str] = None) -> None:
        """记录API请求失败"""
        self.circuit_breaker.record_failure(api_name, error)
    
    def with_retry(self, api_name: str = "default", max_retries: int = 3):
        """
        重试装饰器
        
        使用方式：
            @limiter.with_retry(api_name="stock_spot", max_retries=3)
            def call_api():
                return ak.stock_zh_a_spot_em()
        """
        def decorator(func: Callable):
            @retry(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                retry=retry_if_exception_type((ConnectionError, TimeoutError, Exception)),
                before_sleep=before_sleep_log(logger, logging.WARNING)
            )
            @wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    # 等待速率限制
                    self.wait_before_request(api_name)
                    result = func(*args, **kwargs)
                    self.record_success(api_name)
                    return result
                except Exception as e:
                    self.record_failure(api_name, str(e))
                    raise
            
            return wrapper
        return decorator
    
    def reset(self, api_name: Optional[str] = None) -> None:
        """重置速率限制器和熔断器状态"""
        with self._lock:
            if api_name:
                if api_name in self._request_records:
                    del self._request_records[api_name]
            else:
                self._request_records.clear()
        self.circuit_breaker.reset(api_name)


# 全局速率限制器实例
_global_limiter: Optional[APIRateLimiter] = None
_limiter_lock = threading.Lock()


def get_api_rate_limiter(config: Optional[RateLimitConfig] = None) -> APIRateLimiter:
    """
    获取全局速率限制器实例
    
    Args:
        config: 速率限制配置（可选，仅在首次调用时生效）
    
    Returns:
        APIRateLimiter 实例
    """
    global _global_limiter
    
    with _limiter_lock:
        if _global_limiter is None:
            _global_limiter = APIRateLimiter(config)
    
    return _global_limiter


def reset_global_limiter() -> None:
    """重置全局速率限制器"""
    global _global_limiter
    
    with _limiter_lock:
        if _global_limiter is not None:
            _global_limiter.reset()
            _global_limiter = None


if __name__ == "__main__":
    # 测试代码
    import logging
    logging.basicConfig(level=logging.DEBUG)
    
    print("=" * 80)
    print("API 速率限制器测试")
    print("=" * 80)
    
    limiter = APIRateLimiter()
    
    # 测试1: 速率限制
    print("\n测试1: 速率限制")
    start_time = time.time()
    for i in range(3):
        limiter.wait_before_request("test_api")
        print(f"请求 {i+1}: {time.time() - start_time:.2f}s")
    
    # 测试2: 熔断器
    print("\n测试2: 熔断器")
    for i in range(5):
        if limiter.circuit_breaker.is_available("test_api"):
            print(f"检查 {i+1}: 可用")
            limiter.circuit_breaker.record_failure("test_api", f"测试错误 {i+1}")
        else:
            print(f"检查 {i+1}: 不可用（熔断）")
            break
    
    # 测试3: 重试装饰器
    print("\n测试3: 重试装饰器")
    
    @limiter.with_retry(api_name="retry_test", max_retries=3)
    def test_function():
        if random.random() > 0.7:
            print("调用成功")
            return "success"
        else:
            print("调用失败")
            raise Exception("随机失败")
    
    try:
        result = test_function()
        print(f"最终结果: {result}")
    except Exception as e:
        print(f"最终失败: {e}")
    
    print("\n测试完成")

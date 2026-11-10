"""LLM 并发三池回归（批次2-10，评审 M-1）。

单全局信号量拆为 chat/gen/aux 三池后锁定的不变量：

- 默认上限 chat=2 / gen=1 / aux=4，可被 .env 对应变量覆盖
- set/get 按池独立生效，未知池名报错
- 工厂路由：create_llm_client 默认 chat、pool= 可指定；
  create_gen_llm_client 恒 gen；未知池在构造期即报错
- 池间互不占用：gen 占满不阻塞 chat；aux 池并发独立计数
- 运行时调低上限：在途请求释放旧信号量不炸，新请求排队在新信号量
- images_generate 恒走 gen 池（即使客户端属 chat 池）
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def restore_pools():
    """测试内改池后恢复全局表，避免污染同进程后续测试"""
    from core.llm import client as m

    limits = dict(m._LLM_CONCURRENCY)
    sems = dict(m._LLM_SEMAPHORES)
    yield
    m._LLM_CONCURRENCY.clear()
    m._LLM_CONCURRENCY.update(limits)
    m._LLM_SEMAPHORES.clear()
    m._LLM_SEMAPHORES.update(sems)


def test_defaults_per_review_plan():
    """评审计划：chat=2 / gen=1 / aux=4（.env 未设置时的回退值）"""
    from core.llm import client as m

    assert m.LLM_POOLS == ("chat", "gen", "aux")
    assert m._POOL_ENV_VARS == {
        "chat": ("LLM_MAX_CONCURRENCY", "2"),
        "gen": ("LLM_GEN_MAX_CONCURRENCY", "1"),
        "aux": ("LLM_AUX_MAX_CONCURRENCY", "4"),
    }
    assert {p: m._pool_default(p) for p in m.LLM_POOLS} == {"chat": 2, "gen": 1, "aux": 4}


def test_env_override_and_invalid_fallback(monkeypatch):
    from core.llm import client as m

    monkeypatch.setenv("LLM_AUX_MAX_CONCURRENCY", "7")
    assert m._pool_default("aux") == 7
    monkeypatch.setenv("LLM_AUX_MAX_CONCURRENCY", "0")  # 0/负数钳到 1
    assert m._pool_default("aux") == 1
    monkeypatch.setenv("LLM_GEN_MAX_CONCURRENCY", "abc")  # 非数字回退默认
    assert m._pool_default("gen") == 1


def test_set_get_per_pool_independent(restore_pools):
    from core.llm import client as m

    assert m.set_llm_concurrency(5, "aux") == 5
    assert m.get_llm_concurrency("aux") == 5
    assert m.get_llm_concurrency("chat") != 5  # 其他池不受影响
    # 不传 pool 的旧调用签名 = chat 池（向后兼容 config_routes 旧用法）
    assert m.set_llm_concurrency(3) == 3
    assert m.get_llm_concurrency("chat") == 3

    with pytest.raises(ValueError):
        m.set_llm_concurrency(2, "nope")


def test_factory_pool_routing():
    from core.llm import client as m

    assert m.create_llm_client()._pool == "chat"
    assert m.create_llm_client(pool="aux")._pool == "aux"
    assert m.create_gen_llm_client()._pool == "gen"
    with pytest.raises(ValueError):
        m.OpenAICompatibleClient(pool="bad")


def test_pools_are_isolated(restore_pools):
    """gen 池占满时 chat 池照常放行——这正是 M-1 要解决的问题"""
    from core.llm import client as m

    m.set_llm_concurrency(1, "gen")

    async def scenario():
        gen = m.create_gen_llm_client()
        chat = m.create_llm_client()
        gen_sem = gen._pool_semaphore()
        chat_sem = chat._pool_semaphore()
        assert gen_sem is not chat_sem

        await gen_sem.acquire()  # 占满 gen 池
        assert gen_sem.locked()
        # chat 池不受影响，立即可得
        await asyncio.wait_for(chat_sem.acquire(), timeout=0.5)
        chat_sem.release()
        # aux 池同样独立
        aux_sem = m.create_llm_client(pool="aux")._pool_semaphore()
        await asyncio.wait_for(aux_sem.acquire(), timeout=0.5)
        aux_sem.release()
        gen_sem.release()

    asyncio.run(scenario())


def test_runtime_reconfigure_keeps_inflight_release_safe(restore_pools):
    """set 重建信号量后：在途请求释放旧对象不炸，新请求排队在新对象（上限生效）"""
    from core.llm import client as m

    async def scenario():
        c = m.create_llm_client(pool="aux")
        m.set_llm_concurrency(2, "aux")
        old_sem = c._pool_semaphore()
        await old_sem.acquire()          # 模拟在途请求已持有旧信号量
        m.set_llm_concurrency(1, "aux")  # 调低并重建
        new_sem = c._pool_semaphore()
        assert new_sem is not old_sem
        old_sem.release()                 # 释放旧对象：安全，不抛异常
        # 新请求排队在新信号量上，占满即锁
        await asyncio.wait_for(new_sem.acquire(), timeout=0.5)
        assert new_sem.locked()
        new_sem.release()

    asyncio.run(scenario())


def test_images_generate_uses_gen_pool(restore_pools):
    """图片生成属长生成负载：即使客户端属 chat 池，也恒在 gen 池排队"""
    from core.llm import client as m

    m.set_llm_concurrency(4, "chat")
    m.set_llm_concurrency(1, "gen")

    async def scenario():
        chat_client = m.create_llm_client()  # chat 池客户端
        gen_sem = m._LLM_SEMAPHORES["gen"]
        await gen_sem.acquire()  # 占住 gen 池
        done = asyncio.Event()

        async def run():
            class R:
                data: list = []  # noqa: RUF012

            class FakeClient:
                class ImagesApi:
                    @staticmethod
                    async def generate(**kwargs):
                        done.set()
                        return R()

                images = ImagesApi()

            chat_client._build_client = lambda: FakeClient()
            await chat_client.images_generate(model="x", prompt="p")

        task = asyncio.create_task(run())
        # gen 池被占：images_generate 必须排队等待 → 证明它不在 chat 池
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=0.3)
        gen_sem.release()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())

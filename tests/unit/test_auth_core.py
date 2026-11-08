"""认证核心单元测试 —— 密码策略、令牌、限流。

对应 design-08-01-auth.md 的威胁模型：

- **T3 用户枚举**：失败路径必须恒定耗时、响应不可区分；
- **T2 密码爆破**：限流在窗口内正确计数并锁定；
- **T4 会话劫持**：令牌高熵、只存 hash。
"""

from __future__ import annotations

import time

import pytest

from config.settings import settings
from core.auth.passwords import (
    BCRYPT_HARD_LIMIT,
    PasswordValidationError,
    hash_password,
    needs_rehash,
    password_byte_length,
    validate_password,
    verify_password,
)
from core.auth.ratelimit import LoginThrottle, SlidingWindowCounter
from core.auth.tokens import TOKEN_HASH_LENGTH, generate_session_token, hash_session_token

# ── 密码策略 ────────────────────────────────────────────────────────


class TestPasswordPolicy:
    """密码策略：**8–20 位，且必须同时包含字母和数字**（用户 2026-10-27 指定）。

    长度按**字符数**计，不再是 UTF-8 字节数 —— 见 ``core/auth/passwords.py``
    的模块文档。bcrypt 的 72 字节硬上限仍然守着（最后一条用例）。
    """

    def test_too_short_rejected(self) -> None:
        with pytest.raises(PasswordValidationError, match="8-20 位"):
            validate_password("abc123")

    def test_too_long_rejected(self) -> None:
        with pytest.raises(PasswordValidationError, match="8-20 位"):
            validate_password("a1" + "b" * 19)  # 21 位

    def test_empty_rejected(self) -> None:
        with pytest.raises(PasswordValidationError):
            validate_password("")

    def test_pure_digits_rejected(self) -> None:
        """8 个数字：长度合规、也有数字，但**缺字母**。"""
        with pytest.raises(PasswordValidationError, match="字母"):
            validate_password("12345678")

    def test_pure_letters_rejected(self) -> None:
        with pytest.raises(PasswordValidationError, match="数字"):
            validate_password("abcdefgh")

    def test_symbols_do_not_satisfy_letter_or_digit(self) -> None:
        """只有符号也不行 —— 符号不在"字母 + 数字"的组合要求里。"""
        with pytest.raises(PasswordValidationError, match="字母"):
            validate_password("!!!!!!!!")

    @pytest.mark.parametrize(
        "password",
        [
            "Passw0rd2026",   # 常见组合
            "abc12345",       # 恰好 8 位（下界）
            "a1" + "b" * 18,  # 恰好 20 位（上界）
            "aB3!@#$%^",      # 含符号也允许
        ],
    )
    def test_valid_passwords_accepted(self, password: str) -> None:
        validate_password(password)

    @pytest.mark.parametrize("length", [7, 21])
    def test_boundary_lengths_rejected(self, length: int) -> None:
        """7 位太短、21 位太长 —— 边界逐个钉死。"""
        pw = ("a1" + "b" * 30)[:length]
        if length < 8:
            pw = ("a1" + "b" * 30)[:7]
        with pytest.raises(PasswordValidationError, match="8-20 位"):
            validate_password(pw)

    def test_chinese_characters_allowed_with_letter_and_digit(self) -> None:
        """长度按**字符数**算，所以 6 个汉字 + abc123 = 9 位，合规。

        旧规则（按字节）下这串是 6*3+6 = 24 字节，同样合规；但新规则下
        "24 个汉字"会因**超过 20 位**被拒 —— 这是刻意的行为变化。
        """
        validate_password("密码密码密码1a")  # 8 位：6 汉字 + 1 数字 + 1 字母

    def test_byte_hard_limit_still_enforced(self) -> None:
        """20 位以内**仍可能超 72 字节** —— bcrypt 硬上限必须单独守住。

        构造：18 个 4 字节 Unicode 字母（𐐀 U+10400，Lo 类）+ 2 个 3 字节全角数字，
        = 20 个字符、78 字节。长度合规、组合合规，但 bcrypt 会拒绝。
        """
        pw = "\U00010400" * 18 + "\uff11" * 2
        assert len(pw) == 20
        assert password_byte_length(pw) == 18 * 4 + 2 * 3
        assert password_byte_length(pw) > BCRYPT_HARD_LIMIT
        with pytest.raises(PasswordValidationError, match="多字节"):
            validate_password(pw)


# ── 哈希与校验 ──────────────────────────────────────────────────────


class TestPasswordHashing:
    def test_round_trip(self) -> None:
        pw = "Passw0rd2026"
        hashed = hash_password(pw)
        assert hashed.startswith("$2b$")
        assert verify_password(pw, hashed)
        assert not verify_password("wrong-password", hashed)

    def test_salted_independently(self) -> None:
        pw = "same-password-twice"
        assert hash_password(pw) != hash_password(pw)

    def test_verify_against_missing_hash_returns_false(self) -> None:
        """用户不存在时返回 False —— 但内部仍跑一次 bcrypt（恒定耗时）。

        这里只验证返回值；耗时抹平无法在单测里稳定测量。
        """
        assert verify_password("anything", None) is False
        assert verify_password("anything", "") is False

    def test_verify_over_long_password_does_not_raise(self) -> None:
        """超长密码不能抛异常 —— bcrypt 4.x/5.x 会抛 ValueError，必须被吃掉。"""
        hashed = hash_password("normal-password")
        assert verify_password("a" * 200, hashed) is False

    def test_corrupted_hash_returns_false(self) -> None:
        assert verify_password("whatever", "not-a-bcrypt-hash") is False

    def test_needs_rehash_false_when_rounds_match(self) -> None:
        assert needs_rehash(hash_password("aaaa-bbbb")) is False

    def test_needs_rehash_true_with_lower_rounds(self, monkeypatch) -> None:
        hashed = hash_password("aaaa-bbbb")
        monkeypatch.setattr(settings.auth, "bcrypt_rounds", 12)
        assert needs_rehash(hashed) is True

    def test_needs_rehash_true_for_garbage(self) -> None:
        assert needs_rehash("garbage") is True


# ── 会话令牌 ────────────────────────────────────────────────────────


class TestSessionTokens:
    def test_tokens_are_unique(self) -> None:
        tokens = {generate_session_token() for _ in range(200)}
        assert len(tokens) == 200

    def test_token_is_url_safe_and_long_enough(self) -> None:
        token = generate_session_token()
        assert len(token) >= 40  # 32 字节 base64url ≈ 43 字符
        assert all(c.isalnum() or c in "-_" for c in token)

    def test_hash_is_sha256_hex(self) -> None:
        token = generate_session_token()
        h = hash_session_token(token)
        assert len(h) == TOKEN_HASH_LENGTH == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_is_deterministic_but_token_is_not_stored(self) -> None:
        token = generate_session_token()
        assert hash_session_token(token) == hash_session_token(token)
        # hash 不可逆且不等于明文
        assert hash_session_token(token) != token


# ── 限流 ────────────────────────────────────────────────────────────


class TestSlidingWindowCounter:
    def test_allows_up_to_limit_then_denies(self) -> None:
        counter = SlidingWindowCounter(max_events=3, window_seconds=60)
        for _ in range(3):
            assert counter.hit("ip").allowed
        denied = counter.hit("ip")
        assert not denied.allowed
        assert denied.retry_after >= 1

    def test_keys_are_independent(self) -> None:
        counter = SlidingWindowCounter(max_events=1, window_seconds=60)
        assert counter.hit("a").allowed
        assert not counter.hit("a").allowed
        assert counter.hit("b").allowed  # 另一个 key 不受影响

    def test_window_expiry_frees_quota(self) -> None:
        counter = SlidingWindowCounter(max_events=1, window_seconds=0.05)
        assert counter.hit("k").allowed
        assert not counter.hit("k").allowed
        time.sleep(0.08)
        assert counter.hit("k").allowed


class TestLoginThrottle:
    def test_locks_after_max_failures(self) -> None:
        throttle = LoginThrottle(max_failures=3, window_seconds=60, lock_seconds=60)
        for _ in range(3):
            assert throttle.check("alice").allowed
            throttle.record_failure("alice")

        state = throttle.check("alice")
        assert not state.allowed
        assert state.retry_after >= 1

    def test_success_resets(self) -> None:
        throttle = LoginThrottle(max_failures=3, window_seconds=60, lock_seconds=60)
        throttle.record_failure("alice")
        throttle.record_failure("alice")
        throttle.reset("alice")
        for _ in range(2):
            assert throttle.check("alice").allowed
            throttle.record_failure("alice")

    def test_lock_expires(self) -> None:
        throttle = LoginThrottle(max_failures=1, window_seconds=60, lock_seconds=0.05)
        throttle.record_failure("alice")
        assert not throttle.check("alice").allowed
        time.sleep(0.1)
        assert throttle.check("alice").allowed

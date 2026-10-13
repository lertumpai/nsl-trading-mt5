import uuid
from dataclasses import dataclass, field

import aiohttp

from .errors import BrokerError
from .models import AuthChallenge
from .protocol import LOGIN_URL, LOGOUT_URL, USER_AGENT, loads


@dataclass
class AuthClient:
    login_url: str = LOGIN_URL
    logout_url: str = LOGOUT_URL
    timeout: float = 15.0
    session: aiohttp.ClientSession | None = field(default=None, repr=False)
    _ssid: str | None = field(default=None, repr=False)

    @property
    def ssid(self) -> str:
        if not self._ssid:
            raise BrokerError("SESSION_EXPIRED", "Log in before connecting to the broker.")
        return self._ssid

    async def login(self, identifier: str, secret: str) -> str | AuthChallenge:
        if not identifier.strip() or not secret:
            raise BrokerError("INVALID_CREDENTIALS", "Identifier and password are required.")
        await self.close()
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={"User-Agent": USER_AGENT},
        )
        try:
            async with self.session.post(
                self.login_url,
                json={"identifier": identifier.strip(), "password": secret},
                headers={"Origin": "https://login.iqoption.com"},
                allow_redirects=False,
            ) as response:
                status = response.status
                try:
                    payload = loads(await response.text())
                except (ValueError, UnicodeError):
                    payload = None
                if status == 429:
                    raise BrokerError(
                        "RATE_LIMITED", "Login rate limited; wait before trying again."
                    )
                if status >= 500:
                    raise BrokerError(
                        "NETWORK_UNAVAILABLE", "Broker authentication service is unavailable."
                    )
                if not isinstance(payload, dict):
                    raise BrokerError(
                        "PROTOCOL_UNSUPPORTED",
                        "Login returned an unrecognized response; browser verification may be required.",
                    )
                code = str(payload.get("code", "")).lower()
                challenge_data = (
                    payload.get("data") if isinstance(payload.get("data"), dict) else {}
                )
                text = (str(payload.get("message", "")) + " " + code).lower()
                challenge = any(
                    word in text
                    for word in (
                        "2fa",
                        "two-factor",
                        "verification",
                        "verify",
                        "captcha",
                        "sms",
                        "otp",
                    )
                ) or any(key in challenge_data for key in ("token", "challenge", "verification"))
                if challenge:
                    return AuthChallenge(
                        str(uuid.uuid4()),
                        "browser" if "captcha" in text else "verification",
                        "Additional verification is required. This account's challenge flow has not yet been verified; complete it in the normal IQ Option client.",
                    )
                if (
                    status in (401, 403)
                    or payload.get("success") is False
                    or payload.get("isSuccessful") is False
                    or code not in ("", "0", "200", "success")
                ):
                    raise BrokerError(
                        "INVALID_CREDENTIALS",
                        "Login failed. Check credentials and account verification in IQ Option.",
                    )
                if status != 200:
                    raise BrokerError(
                        "PROTOCOL_UNSUPPORTED", "Unexpected authentication response status."
                    )
                token = payload.get("ssid") or challenge_data.get("ssid")
                if not token:
                    token = next(
                        (
                            cookie.value
                            for cookie in self.session.cookie_jar
                            if cookie.key == "ssid"
                        ),
                        None,
                    )
                if not isinstance(token, str) or not token:
                    raise BrokerError(
                        "PROTOCOL_UNSUPPORTED",
                        "Authentication returned no recognized session token.",
                    )
                self._ssid = token
                return token
        except (TimeoutError, aiohttp.ClientError):
            await self.close()
            raise BrokerError(
                "NETWORK_TIMEOUT", "Could not reach the authentication service securely."
            ) from None
        except BaseException:
            await self.close()
            raise

    async def complete_challenge(self, challenge_id: str, code: str) -> None:
        raise BrokerError(
            "PROTOCOL_UNSUPPORTED",
            "OTP completion is unavailable until this account's challenge protocol is verified.",
        )

    async def logout(self) -> bool:
        invalidated = False
        try:
            if self.session is not None and self._ssid:
                async with self.session.post(
                    self.logout_url, cookies={"ssid": self._ssid}, allow_redirects=False
                ) as response:
                    if response.status == 204:
                        invalidated = True
                    elif response.status == 200:
                        try:
                            payload = loads(await response.text())
                            invalidated = isinstance(payload, dict) and (
                                payload.get("success") is True
                                or payload.get("isSuccessful") is True
                                or payload.get("code") == "success"
                            )
                        except ValueError:
                            pass
        except (TimeoutError, aiohttp.ClientError):
            pass
        finally:
            await self.close()
        return invalidated

    async def close(self) -> None:
        self._ssid = None
        if self.session is not None:
            await self.session.close()
            self.session = None

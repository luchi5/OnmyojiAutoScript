# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import onepush.core
import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response
from smtplib import SMTPResponseException

from module.logger import logger


class _SafeProviderLogger:
    """OnePush logs raw responses and request exceptions containing credentials."""

    def debug(self, *args, **kwargs):
        # A response may contain private message content or access credentials.
        pass

    def error(self, message, *args, **kwargs):
        kind = type(message).__name__ if isinstance(message, BaseException) else 'ProviderError'
        logger.warning(f'OnePush request error: {kind}')


onepush.core.log = _SafeProviderLogger()


class Notifier:
    def __init__(self, _config: str, enable: bool=False) -> None:
        self.config_name: str = ""
        self.enable: bool = enable
        self.config = {}
        self.provider_name = None
        self.notifier = None
        self.required = []

        if not self.enable:
            return
        config = {}
        try:
            for item in yaml.safe_load_all(_config):
                config.update(item)
        except Exception:
            logger.error("Fail to load onepush config, skip sending")
            return
        self.config = config
        try:
            # 获取provider
            self.provider_name: str = self.config.pop("provider", None)
            if self.provider_name is None:
                logger.info("No provider specified, skip sending")
                return
            # 获取notifier
            self.notifier: Provider = get_notifier(self.provider_name)
            # 获取notifier的必填参数
            self.required: list[str] = self.notifier.params["required"]
        except OnePushException:
            logger.warning("Init notifier failed: OnePushException")
            return
        except Exception as e:
            logger.warning(f'Init notifier failed: {type(e).__name__}')
            return

    def push(self, **kwargs) -> bool:
        if not self.enable:
            return False
        if self.notifier is None:
            logger.warning('Push notify failed: notifier unavailable')
            return False
        # 更新配置
        kwargs["title"] = f"{self.config_name} {kwargs['title']}"
        self.config.update(kwargs)
        # pre check
        for key in self.required:
            if key not in self.config:
                logger.warning(
                    f"Notifier {type(self.notifier).__name__} require param '{key}' but not provided"
                )


        if isinstance(self.notifier, Custom):
            if "method" not in self.config or self.config["method"] == "post":
                self.config["datatype"] = "json"
            if not ("data" in self.config or isinstance(self.config["data"], dict)):
                self.config["data"] = {}
            if "title" in kwargs:
                self.config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                self.config["data"]["content"] = kwargs["content"]

        if self.provider_name.lower() == "gocqhttp":
            access_token = self.config.get("access_token")
            if access_token:
                self.config["token"] = access_token


        try:
            resp = self.notifier.notify(**self.config)
            if resp is None:
                logger.warning(f'Push notify failed: no response ({type(self.notifier).__name__})')
                return False
            if isinstance(resp, Response):
                if resp.status_code != 200:
                    logger.warning("Push notify failed!")
                    logger.warning(f"HTTP Code:{resp.status_code}")
                    return False
                if self.provider_name.lower() in ('pushplus', 'bark'):
                    receipt_provider = {'pushplus': 'PushPlus', 'bark': 'Bark'}[self.provider_name.lower()]
                    return_data = resp.json()
                    if not isinstance(return_data, dict):
                        logger.warning(f'{receipt_provider} request rejected: invalid response object')
                        return False
                    code = return_data.get('code')
                    if type(code) is not int:
                        logger.warning(f'{receipt_provider} request rejected: invalid code type ({type(code).__name__})')
                        return False
                    if code != 200:
                        reasons = {900: '请求受限', 903: '令牌无效', 905: '未实名'} if receipt_provider == 'PushPlus' else {}
                        reason = reasons.get(code, '业务拒绝')
                        logger.warning(f'{receipt_provider} request rejected: code={code}, reason={reason}')
                        return False
                    logger.info(f'{receipt_provider} 请求已受理，最终送达未确认')
                    return True
                if self.provider_name.lower() == "gocqhttp":
                    return_data: dict = resp.json()
                    if return_data["status"] == "failed":
                        logger.warning('Push notify failed: gocqhttp status=failed')
                        return False
            elif self.provider_name.lower() in ('pushplus', 'bark'):
                receipt_provider = {'pushplus': 'PushPlus', 'bark': 'Bark'}[self.provider_name.lower()]
                logger.warning(f'{receipt_provider} request rejected: response is not HTTP')
                return False
        except SMTPResponseException as e:
            logger.warning(f'Push notify failed: {type(e).__name__}')
            return False
        except OnePushException as e:
            logger.warning(f'Push notify failed: {type(e).__name__}')
            return False
        except Exception as e:
            logger.warning(f'Push notify failed: {type(e).__name__}')
            return False

        logger.info("Push notify success")
        return True




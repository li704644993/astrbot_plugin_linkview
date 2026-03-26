import io
import os
import re
import html
import random
import asyncio
import tempfile
from typing import Optional, Tuple, List
from urllib.parse import quote, urljoin

import httpx
from PIL import Image as PILImage
from bs4 import BeautifulSoup

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

# --- 配置 ---
LINK_API_ENDPOINT = "https://whatslink.info/api/v1/link"
CILISOU_BASE_URL = "https://cilisousuo.com"

# 图片安全限制
MAX_IMAGE_RESPONSE_SIZE = 20 * 1024 * 1024  # 单张图片最大 20MB
MAX_IMAGE_PIXELS = 8192 * 8192              # 最大像素数（约 67MP）
DOWNLOAD_CONCURRENCY = 5                     # 截图并发下载数

# 提前配置 PIL 像素上限，防止解压炸弹
PILImage.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# 磁力链接正则（匹配消息中任意位置）
MAGNET_PATTERN = re.compile(r"magnet:\?xt=urn:[a-zA-Z0-9]+:[a-zA-Z0-9]+")
CILISOU_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "zh-CN,zh;q=0.9",
    "referer": CILISOU_BASE_URL,
}


@register("linkview", "liting", "磁力链接解析与种子搜索插件", "1.0.0", "https://github.com/your/repo")
class LinkViewPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 临时图片存放目录
        self.temp_dir = os.path.join(tempfile.gettempdir(), "linkview_images")
        # 共享 HTTP 客户端（连接复用，避免重复 TLS 握手）
        self._http_client: Optional[httpx.AsyncClient] = None
        # 并发下载信号量（限制同时下载的截图数量）
        self._download_semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建共享的 HTTP 客户端"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=20.0)
        return self._http_client

    async def initialize(self):
        """插件初始化，创建临时目录"""
        os.makedirs(self.temp_dir, exist_ok=True)
        # 预创建共享 HTTP 客户端
        self._http_client = httpx.AsyncClient(timeout=20.0)
        # 日志输出当前配置
        enabled = self.config.get("enable", False)
        whitelist = self.config.get("group_whitelist", [])
        logger.info(f"LinkView 插件已加载！解析功能: {'开启' if enabled else '关闭'}，白名单群: {whitelist}")

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        """检查当前消息是否允许触发解析功能。
        
        逻辑：
        1. 如果 enable=False，全局禁用（指令也不响应）
        2. 如果 group_whitelist 为空列表，则所有群/私聊都允许
        3. 如果 group_whitelist 非空，则只有白名单中的群聊允许（私聊也放行）
        """
        if not self.config.get("enable", False):
            return False
        
        whitelist = self.config.get("group_whitelist", [])
        if not whitelist:
            # 白名单为空 = 不限制
            return True
        
        group_id = event.get_group_id()
        if not group_id:
            # 私聊消息，放行
            return True
        
        # 白名单中的值统一转为字符串比较
        whitelist_str = [str(g) for g in whitelist]
        return str(group_id) in whitelist_str

    # ========================
    #    工具方法
    # ========================

    async def add_noise_to_image(self, image_url: str) -> Optional[str]:
        """下载图片并添加随机像素噪点，返回临时文件路径。

        安全措施：
        - 使用 Semaphore 限制并发下载数
        - 校验 Content-Type 和响应体大小
        - PIL 已全局配置像素上限（MAX_IMAGE_PIXELS）
        """
        async with self._download_semaphore:
            try:
                client = await self._get_http_client()
                resp = await client.get(image_url, timeout=20.0)
                resp.raise_for_status()

                # 校验 Content-Type
                content_type = resp.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    logger.warning(f"图片 {image_url} Content-Type 非图片类型: {content_type}")
                    return None

                # 校验响应体大小
                if len(resp.content) > MAX_IMAGE_RESPONSE_SIZE:
                    logger.warning(f"图片 {image_url} 体积超限: {len(resp.content)} bytes")
                    return None

                image = PILImage.open(io.BytesIO(resp.content)).convert("RGB")
                width, height = image.size

                # 添加 5 个随机噪点
                for _ in range(5):
                    rand_x = random.randint(0, width - 1)
                    rand_y = random.randint(0, height - 1)
                    rand_color = (
                        random.randint(0, 255),
                        random.randint(0, 255),
                        random.randint(0, 255),
                    )
                    image.putpixel((rand_x, rand_y), rand_color)

                # 保存到临时文件
                temp_path = os.path.join(self.temp_dir, f"img_{random.randint(100000, 999999)}.png")
                image.save(temp_path, format="PNG")
                return temp_path
            except Exception as e:
                logger.warning(f"处理图片 {image_url} 时失败: {e}")
                return None

    @staticmethod
    async def get_magnet_from_cilisou(search_query: str) -> Optional[str]:
        """从 cilisou 搜索并提取第一条结果的磁力链接"""
        try:
            async with httpx.AsyncClient(headers=CILISOU_HEADERS, timeout=15) as client:
                search_url = f"{CILISOU_BASE_URL}/search?q={quote(search_query)}"
                logger.info(f"种子搜索: 访问 {search_url}")

                resp_a = await client.get(search_url)
                resp_a.raise_for_status()

                soup_a = BeautifulSoup(resp_a.text, "lxml")
                link_tag = soup_a.select_one("ul.list .item .link")

                if not (link_tag and link_tag.get("href")):
                    logger.warning("种子搜索: 未找到结果链接。")
                    return None

                detail_url = urljoin(CILISOU_BASE_URL, link_tag["href"])
                logger.info(f"种子搜索: 找到详情页 {detail_url}")

                resp_b = await client.get(detail_url)
                resp_b.raise_for_status()

                soup_b = BeautifulSoup(resp_b.text, "lxml")
                input_tag = soup_b.select_one("#input-magnet")

                if not (input_tag and input_tag.get("value")):
                    logger.warning("种子搜索: 未找到磁力链接。")
                    return None

                magnet_link = html.unescape(input_tag["value"])
                logger.info("种子搜索: 成功提取磁力链接！")
                return magnet_link
        except Exception as e:
            logger.error(f"种子搜索爬虫失败: {e}")
            return None

    async def _do_parse(self, event: AstrMessageEvent, original_url: str) -> Tuple[Optional[list], str, List[str]]:
        """核心解析逻辑：调用 whatslink API 解析磁力链接，获取截图并构建消息组件。

        返回三元组 (chain_or_nodes, summary_text, temp_files)：
        - chain_or_nodes: 构建好的消息组件列表（Node 列表或普通组件列表），None 表示无图可发
        - summary_text: 汇总文本
        - temp_files: 需要在发送完成后清理的临时文件路径列表
        """
        # 清理 URL（去掉 & 后面的参数）
        ampersand_index = original_url.find("&")
        cleaned_url = original_url[:ampersand_index] if ampersand_index != -1 else original_url

        api_headers = {
            "Referer": "https://whatslink.info/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        # 调用 API 解析
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {"url": cleaned_url}
                response = await client.get(LINK_API_ENDPOINT, params=params, headers=api_headers)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"调用链接解析API时出错: {e}")
            return None, "解析失败，请检查链接或稍后再试。", []

        # 检查 API 错误
        api_error = data.get("error")
        if api_error:
            return None, f"解析失败：API返回错误 - {api_error}", []

        # 提取文件信息
        file_name = data.get("name", "未知")
        file_size = data.get("size", 0)
        file_count = data.get("count", 0)
        size_gb = file_size / (1024 * 1024 * 1024)

        # 获取截图列表
        screenshots = data.get("screenshots")
        total_found = len(screenshots) if isinstance(screenshots, list) else 0

        if not total_found:
            return None, (
                f"文件名：{file_name}\n"
                f"总大小：{size_gb:.2f} GB\n"
                f"文件数：{file_count}\n"
                f"但链接中未找到任何预览截图。"
            ), []

        # 并发下载并加噪图片
        tasks = [
            self.add_noise_to_image(shot["screenshot"])
            for shot in screenshots
            if isinstance(shot, dict) and "screenshot" in shot
        ]
        processed_images = await asyncio.gather(*tasks)

        # 收集成功处理的图片路径
        noise_added_count = 0
        temp_files = []
        valid_images = []

        for img_path in processed_images:
            if img_path:
                valid_images.append(img_path)
                temp_files.append(img_path)
                noise_added_count += 1

        if not valid_images:
            return None, (
                f"解析完成，链接共包含 {total_found} 张截图，但全部下载失败。"
            ), []

        # 获取 bot 名称，用于合并转发消息的显示
        bot_name = self.config.get("forward_bot_name", "LinkView")
        try:
            bot_uin = int(self.config.get("forward_bot_uin", 0))
        except (ValueError, TypeError):
            logger.warning("forward_bot_uin 配置值无效，已回退为 0")
            bot_uin = 0
        use_forward = self.config.get("use_forward_message", True)

        if use_forward and event.get_group_id():
            # 群聊 + 开启合并转发：构建 Nodes 容器
            # 关键：必须用 Comp.Nodes 把所有 Node 包在一起，
            # 否则框架会逐个 Node 调用 send_group_forward_msg，变成多条独立转发
            node_list = []

            # 第一条 Node：磁力链接 + 文件信息
            info_text = (
                f"🔗 {cleaned_url}\n\n"
                f"📄 文件名：{file_name}\n"
                f"📦 总大小：{size_gb:.2f} GB\n"
                f"📁 文件数：{file_count}\n"
                f"🖼️ 截图数：{total_found}"
            )
            node_list.append(Comp.Node(
                uin=bot_uin,
                name=bot_name,
                content=[Comp.Plain(info_text)]
            ))

            # 每张截图作为一条 Node
            for img_path in valid_images:
                node_list.append(Comp.Node(
                    uin=bot_uin,
                    name=bot_name,
                    content=[Comp.Image.fromFileSystem(img_path)]
                ))

            # 用 Nodes 容器包裹所有 Node，框架会一次性调用 send_group_forward_msg
            chain = [Comp.Nodes(node_list)]
        else:
            # 私聊或关闭合并转发：普通消息链
            chain = [Comp.Plain(cleaned_url)]
            for img_path in valid_images:
                chain.append(Comp.Image.fromFileSystem(img_path))

        summary = (
            f"解析完成，链接共包含 {total_found} 张截图，"
            f"已成功发送 {noise_added_count} 张。\n"
            f"已成功对 {noise_added_count} 张图片加入噪音。"
        )

        return chain, summary, temp_files

    def _cleanup_temp_files(self, temp_files: List[str]):
        """清理临时文件"""
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass

    # ========================
    #    指令 Handler
    # ========================

    @filter.command("种子搜索")
    async def seed_search(self, event: AstrMessageEvent):
        """搜索关键词并解析第一条结果的磁力链接。用法：/种子搜索 关键词"""
        if not self._is_allowed(event):
            return  # 静默忽略，不响应
        
        # 获取指令后面的参数文本
        search_term = event.message_str.strip()
        if not search_term:
            yield event.plain_result("请输入要搜索的内容，例如：/种子搜索 关键词")
            return

        yield event.plain_result(f"正在为你搜索「{search_term}」，请稍候...")

        magnet_link = await self.get_magnet_from_cilisou(search_term)
        if not magnet_link:
            yield event.plain_result(f"搜索「{search_term}」失败，没有找到任何结果或网站访问出错。")
            return

        # 解析找到的磁力链接
        chain, summary, temp_files = await self._do_parse(event, magnet_link)
        try:
            if chain:
                yield event.chain_result(chain)
            yield event.plain_result(summary)
        finally:
            self._cleanup_temp_files(temp_files)

    @filter.regex(r"magnet:\?xt=urn:[a-zA-Z0-9]+:[a-zA-Z0-9]+")
    async def magnet_auto_parse(self, event: AstrMessageEvent):
        """自动检测并解析消息中的磁力链接（支持消息任意位置）"""
        if not self._is_allowed(event):
            return  # 静默忽略，不响应
        
        # 从消息中提取磁力链接（支持任意位置匹配）
        match = MAGNET_PATTERN.search(event.message_str)
        if not match:
            return
        magnet_url = match.group(0).strip()
        logger.info(f"检测到磁力链接: {magnet_url[:60]}...")

        yield event.plain_result("收到，正在自动解析磁力链接...")

        chain, summary, temp_files = await self._do_parse(event, magnet_url)
        try:
            if chain:
                yield event.chain_result(chain)
            yield event.plain_result(summary)
        finally:
            self._cleanup_temp_files(temp_files)

    async def terminate(self):
        """插件销毁，关闭 HTTP 客户端并清理临时目录"""
        # 关闭共享 HTTP 客户端
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
        # 清理临时图片目录
        import shutil
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except OSError:
            pass
        logger.info("LinkView 插件已卸载。")

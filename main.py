import io
import os
import html
import random
import asyncio
import tempfile
from typing import Optional
from urllib.parse import quote, urljoin

import httpx
from PIL import Image as PILImage
from bs4 import BeautifulSoup

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

# --- 配置 ---
LINK_API_ENDPOINT = "https://whatslink.info/api/v1/link"
CILISOU_BASE_URL = "https://cilisousuo.com"
CILISOU_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept-language": "zh-CN,zh;q=0.9",
    "referer": CILISOU_BASE_URL,
}


@register("linkview", "liting", "磁力链接解析与种子搜索插件", "1.0.0", "https://github.com/your/repo")
class LinkViewPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 临时图片存放目录
        self.temp_dir = os.path.join(tempfile.gettempdir(), "linkview_images")

    async def initialize(self):
        """插件初始化，创建临时目录"""
        os.makedirs(self.temp_dir, exist_ok=True)
        logger.info("LinkView 插件已加载！")

    # ========================
    #    工具方法
    # ========================

    async def add_noise_to_image(self, image_url: str) -> Optional[str]:
        """下载图片并添加随机像素噪点，返回临时文件路径"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_url, timeout=20.0)
                resp.raise_for_status()

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

    async def _do_parse_and_send(self, event: AstrMessageEvent, original_url: str):
        """核心解析逻辑：调用 whatslink API 解析磁力链接，获取截图并发送

        返回一个 MessageEventResult 列表（用于外部 yield）。
        """
        results = []

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
            results.append(event.plain_result("解析失败，请检查链接或稍后再试。"))
            return results

        # 检查 API 错误
        api_error = data.get("error")
        if api_error:
            results.append(event.plain_result(f"解析失败：API返回错误 - {api_error}"))
            return results

        # 获取截图列表
        screenshots = data.get("screenshots")
        total_found = len(screenshots) if isinstance(screenshots, list) else 0

        if not total_found:
            file_name = data.get("name", "未知")
            file_size = data.get("size", 0)
            file_count = data.get("count", 0)
            size_gb = file_size / (1024 * 1024 * 1024)
            results.append(event.plain_result(
                f"文件名：{file_name}\n"
                f"总大小：{size_gb:.2f} GB\n"
                f"文件数：{file_count}\n"
                f"但链接中未找到任何预览截图。"
            ))
            return results

        # 并发下载并加噪图片
        tasks = [
            self.add_noise_to_image(shot["screenshot"])
            for shot in screenshots
            if isinstance(shot, dict) and "screenshot" in shot
        ]
        processed_images = await asyncio.gather(*tasks)

        # 构建消息链：先发磁力链接文本，再逐张发图片
        chain = [Comp.Plain(cleaned_url)]
        noise_added_count = 0
        temp_files = []

        for img_path in processed_images:
            if img_path:
                chain.append(Comp.Image.fromFileSystem(img_path))
                temp_files.append(img_path)
                noise_added_count += 1

        if len(chain) > 1:
            results.append(event.chain_result(chain))

        # 汇总信息
        summary_text = (
            f"解析完成，链接共包含 {total_found} 张截图，"
            f"已成功发送 {noise_added_count} 张。\n"
            f"已成功对 {noise_added_count} 张图片加入噪音。"
        )
        results.append(event.plain_result(summary_text))

        # 清理临时文件
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass

        return results

    # ========================
    #    指令 Handler
    # ========================

    @filter.command("种子搜索")
    async def seed_search(self, event: AstrMessageEvent):
        """搜索关键词并解析第一条结果的磁力链接。用法：/种子搜索 关键词"""
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
        results = await self._do_parse_and_send(event, magnet_link)
        for result in results:
            yield result

    @filter.regex(r"^magnet:\?xt=urn:[a-zA-Z0-9]+:[a-zA-Z0-9]+")
    async def magnet_auto_parse(self, event: AstrMessageEvent):
        """自动检测并解析消息中的磁力链接"""
        magnet_url = event.message_str.strip()
        logger.info(f"检测到磁力链接: {magnet_url[:60]}...")

        yield event.plain_result("收到，正在自动解析磁力链接...")

        results = await self._do_parse_and_send(event, magnet_url)
        for result in results:
            yield result

    async def terminate(self):
        """插件销毁，清理临时目录"""
        import shutil
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except OSError:
            pass
        logger.info("LinkView 插件已卸载。")

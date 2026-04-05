"""
A component which allows you to parse http://www.qiyoujiage.com/ get oil price
Support individual sensors and history trend.
"""
import re
import asyncio
import logging
import datetime
import voluptuous as vol
# 核心修改：引入 SensorEntity
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA, 
    SensorEntity, 
    SensorStateClass, 
    SensorDeviceClass
)
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME, CONF_REGION
import requests
from bs4 import BeautifulSoup

__version__ = '0.2.4'
_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['requests', 'beautifulsoup4', 'lxml']

# 设置每 6 小时触发一次更新
SCAN_INTERVAL = datetime.timedelta(hours=6)
ICON = 'mdi:gas-station'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_REGION): cv.string,
})

async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the oil price sensors."""
    _LOGGER.info("Setting up OilPrice sensors")
    
    region = config[CONF_REGION]
    base_name = config[CONF_NAME]
    
    data_updater = OilDataUpdater(hass, region)
    
    # 初始化时先更新一次数据
    await data_updater.async_update()
    
    sensors = []
    # 创建 4 个独立油价传感器 (92, 95, 98, 0)
    for oil_type in ["92", "95", "98", "0"]:
        sensors.append(OilPriceIndividualSensor(data_updater, f"{base_name}_{oil_type}", region, oil_type))
    
    # 创建 1 个汇总描述传感器
    sensors.append(OilPriceSummarySensor(data_updater, f"{base_name}_summary", region))
    
    async_add_devices(sensors, True)


class OilDataUpdater:
    """数据抓取协调器"""
    def __init__(self, hass, region):
        self.hass = hass
        self.region = region
        self.data = {}
        self._last_update = None
        self._lock = asyncio.Lock()

    async def async_update(self):
        async with self._lock:
            now = datetime.datetime.now()
            if self._last_update and (now - self._last_update).total_seconds() < 60:
                return

            _LOGGER.debug(f"Fetching new oil prices for {self.region}")
            result = await self.hass.async_add_executor_job(self._fetch_data)
            if result:
                self.data = result
                self._last_update = now

    def _fetch_data(self):
        try:
            header = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            }
            url = f'http://www.qiyoujiage.com/{self.region}.shtml'
            response = requests.get(url, headers=header, timeout=15)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, "lxml")
            
            dls = soup.select("#youjia > dl")
            prices = {}
            for dl in dls:
                dts = dl.select('dt')
                dds = dl.select('dd')
                if dts and dds:
                    dt_text = dts[0].text
                    match = re.search(r"\d+", dt_text)
                    if match:
                        key = match.group()
                        prices[key] = dds[0].text.strip()
            
            summary = "未知"
            tips = ""
            summary_divs = soup.select("#youjiaCont > div")
            if len(summary_divs) >= 2:
                target_div = summary_divs[1]
                tips_span = target_div.find("span")
                if tips_span:
                    tips = tips_span.get_text(strip=True)
                
                full_text = target_div.get_text(strip=True)
                if tips:
                    summary = full_text.replace(tips, "").strip()
                else:
                    summary = full_text

                if summary and not summary.endswith("。"):
                    summary += "。"
            
            return {
                "prices": prices,
                "summary": summary,
                "tips": tips,
                "time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            _LOGGER.error(f"Error fetching oil price data: {e}")
            return self.data


class OilPriceIndividualSensor(SensorEntity):
    """单个油价传感器类 - 继承自 SensorEntity"""
    def __init__(self, updater, name, region, oil_type):
        self._updater = updater
        self._attr_name = name
        self._region = region
        self._oil_type = oil_type
        
        # 使用 _attr_ 方式定义属性，确保初始化时就加载
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = "元/升"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_unique_id = f"oil_price_{region}_{oil_type}"

    @property
    def native_value(self):
        """返回传感器的状态值"""
        prices = self._updater.data.get("prices", {})
        val = prices.get(self._oil_type, "unknown")
        try:
            return float(val)
        except (ValueError, TypeError):
            return val

    @property
    def extra_state_attributes(self):
        """返回额外的属性"""
        return {
            "region": self._region,
            "oil_type": self._oil_type,
            "tips": self._updater.data.get("tips"),
            "update_time": self._updater.data.get("time")
        }

    async def async_update(self):
        await self._updater.async_update()


class OilPriceSummarySensor(Entity):
    """汇总传感器类 - 仅作为文本显示，保持 Entity 基类即可"""
    def __init__(self, updater, name, region):
        self._updater = updater
        self._attr_name = name
        self._region = region
        self._attr_icon = ICON
        self._attr_unique_id = f"oil_price_{region}_summary"

    @property
    def state(self):
        return self._updater.data.get("summary")

    @property
    def extra_state_attributes(self):
        attrs = self._updater.data.get("prices", {}).copy()
        attrs["tips"] = self._updater.data.get("tips")
        attrs["update_time"] = self._updater.data.get("time")
        return attrs

    async def async_update(self):
        await self._updater.async_update()
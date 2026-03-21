# 简介
抓取最新的油价信息，包括油价涨跌提醒（默认1小时更新一次数据）

数据源地址： http://www.qiyoujiage.com

# 安装
1 手动安装，两个文件夹分别 放入 <config directory> /custom_components 和 /packages目录,

并修改 packages/latest_fuel_prices.yaml 文件内为自己本省拼音名字

如  region: shanghai

2 hacs安装 CUSTOM REPOSITORIES中填入：https://github.com/db-one/Latest_Fuel_Prices

# 配置
**Example configuration.yaml:**
```yaml
# 加载自定义配置文件
homeassistant:
  packages: !include_dir_named packages

```


# 前台界面
原始的界面是这样的

![avatar](https://github.com/db-one/Latest_Fuel_Prices/blob/master/2.PNG)

~~ 1-5中样式类型在lovelace目录对应文件中，复制到卡片代码编辑器即可

![avatar](https://github.com/db-one/Latest_Fuel_Prices/blob/master/1.PNG)


# 功能变化
针对原版独立出来了 92 95 98 0 号汽油的传感器，点击可以弹出油价历史在更多历史中可以用HASS的功能直观展现全年的油价涨跌趋势
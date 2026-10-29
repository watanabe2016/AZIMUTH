# AZIMUTH 官网

正式网址：<https://azimuth.xin/>。

这是无需构建的 HTML、CSS、JavaScript 静态网站，包含首页、插件市场和皮肤市场。首页展示软件功能、真实软件录屏、安装包下载和赞赏入口；外观支持浅色、深色及跟随系统。

从仓库根目录预览：

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory website
```

部署时将本目录内容作为静态站点根目录。Windows 安装包不进入 Git 历史，当前由官网独立下载目录提供；自行部署时请配置下载地址。下载次数模块尚未接入真实统计服务。

正式站点使用 HTTPS，页脚展示备案号并链接工信部。源码不包含生产服务器的地址、账户、私钥、证书、部署脚本或备份。

网站自有代码按本目录 LICENSE 使用；截图、录屏、字体、标志及赞赏二维码的许可范围见仓库根目录 THIRD_PARTY_NOTICES.md。

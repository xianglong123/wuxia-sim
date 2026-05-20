#!/bin/bash
# 🏯 江湖经营 - 一键部署脚本
# 用法:  bash deploy.sh
# 作用:  将本地最新代码同步到阿里云服务器，重启服务

set -e

SERVER="root@47.94.159.91"
REMOTE_DIR="/root/wuxia-sim"
PORT=5002

echo "╔═══════════════════════════════════════╗"
echo "║     🏯 江湖经营 · 一键部署            ║"
echo "╚═══════════════════════════════════════╝"

# 1. 检查本地 git 状态
echo ""
echo "📋 [1/4] 检查本地代码状态..."
cd "$(dirname "$0")"
if ! git diff --quiet 2>/dev/null; then
    echo "  ⚠️  有未提交的改动，建议先 git commit"
    read -p "  继续部署？(y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "  ❌ 已取消"
        exit 1
    fi
fi
echo "  ✅ 代码状态检查通过"
echo "  最新 commit: $(git log --oneline -1 2>/dev/null || echo 'N/A')"

# 2. rsync 同步文件到服务器
echo ""
echo "📦 [2/4] 同步文件到服务器..."
rsync -avz --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.db' \
    --exclude='.gitkeep' \
    ./ "$SERVER:$REMOTE_DIR/"
echo "  ✅ 文件同步完成"

# 3. 重启 systemd 服务
echo ""
echo "🔄 [3/4] 重启服务..."
ssh "$SERVER" "systemctl restart wuxia-sim"
sleep 2
if ssh "$SERVER" "systemctl is-active wuxia-sim" | grep -q active; then
    echo "  ✅ 服务已重启 (active)"
else
    echo "  ❌ 服务启动失败！检查日志：ssh $SERVER 'journalctl -u wuxia-sim -n 20 --no-pager'"
    exit 1
fi

# 4. 验证 HTTP 响应
echo ""
echo "🔍 [4/4] 验证服务..."
HTTP_CODE=$(ssh "$SERVER" "curl -sk -o /dev/null -w '%{http_code}' https://ip.xianglong.vip/wuxia/ 2>&1")
echo "  HTTP 状态码: $HTTP_CODE"
if [[ "$HTTP_CODE" == "302" || "$HTTP_CODE" == "200" ]]; then
    echo "  ✅ 部署成功！"
    echo ""
    echo "  🌐 https://ip.xianglong.vip/wuxia/"
    echo "  📝 日志: ssh $SERVER 'journalctl -u wuxia-sim -f'"
else
    echo "  ⚠️  状态码异常，请检查"
fi

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║     ✅ 部署完成                        ║"
echo "╚═══════════════════════════════════════╝"

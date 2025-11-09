#!/bin/bash
# اسکریپت برای دانلود امن فایل‌ها

echo "🔐 راهنمای دانلود امن فایل‌ها"
echo "════════════════════════════════════"
echo ""
echo "از کامپیوتر خودت این دستور رو اجرا کن:"
echo ""
echo "scp $(whoami)@$(hostname -I | awk '{print $1}'):/workspace/keys.json ./"
echo ""
echo "یا با cat کپی کن:"
echo ""
cat /workspace/keys.json
echo ""
echo "════════════════════════════════════"
echo "⚠️  هیچ‌وقت این فایل رو Public نکن!"
echo "════════════════════════════════════"

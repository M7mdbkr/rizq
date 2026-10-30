#!/bin/bash
# رزق POS — تشغيل بنقرة مزدوجة على ماك.
# يشغّل خادمًا محليًا ويفتح المتصفح. أوقف بإغلاق النافذة أو Ctrl+C.
cd "$(dirname "$0")" || exit 1
PORT=4173
echo "================================================"
echo "  رزق POS يعمل الآن"
echo "  على هذا الجهاز:   http://localhost:$PORT"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
[ -n "$IP" ] && echo "  من الآيباد/الجوال: http://$IP:$PORT"
echo "  (نفس شبكة الواي فاي)"
echo "================================================"
( sleep 1 && open "http://localhost:$PORT" ) &
python3 -m http.server "$PORT"

#!/bin/bash
# رزق — تشغيل النظام كامل بنقرة مزدوجة (الكاشير + الوكيل الذكي).
# هذا الملف يشغّل خادم الوكيل على المنفذ 8000، وهو الوحيد اللي يخدم
# الكاشير والـ API مع بعض. ملف rizq-pos/start.command القديم يشغّل
# الكاشير فقط على 4173 بدون الوكيل، فلا تستخدمه لو تبي المساعد يشتغل.
# للإيقاف: أغلق النافذة أو اضغط Ctrl+C، أو شغّل "إيقاف رزق.command".

cd "$(dirname "$0")/agent/runtime" || { echo "ما لقيت مجلد agent/runtime"; read -r; exit 1; }

PORT=8000
URL="http://127.0.0.1:$PORT/pos/"

printf '\033]0;رزق\007'
clear
echo "================================================================"
echo "   رزق — نظام الكاشير + الوكيل الذكي"
echo "================================================================"
echo

# ---------------------------------------------------------------- بايثون
if ! command -v python3 >/dev/null 2>&1; then
  echo "  ✗ python3 غير مثبّت. ثبّته من https://www.python.org ثم أعد المحاولة."
  echo; read -r -p "اضغط Enter للإغلاق..."; exit 1
fi

# ---------------------------------------------------------------- أولاما
if curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  MODEL=$(python3 -c "from rizq_agent import copilot; print(copilot.local_model() or '')" 2>/dev/null)
  if [ -n "$MODEL" ]; then
    echo "  ✓ النموذج المحلي جاهز: $MODEL"
  else
    echo "  ! أولاما شغّال لكن ما فيه نموذج مدعوم. نزّل واحد:  ollama pull qwen3:8b"
  fi
else
  echo "  ! أولاما مو شغّال — الوكيل بيرد بقواعد ثابتة بدون نموذج ذكاء."
  echo "    لتشغيله:  افتح تطبيق Ollama، أو نفّذ في الترمنال:  ollama serve"
fi

# ---------------------------------------------------------------- المنفذ
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -s --max-time 5 "http://127.0.0.1:$PORT/api/health" | grep -q '"ok"'; then
    echo "  ✓ رزق شغّال أصلًا على المنفذ $PORT — بنفتح المتصفح بس."
    echo
    echo "   افتح:  $URL"
    open "$URL" 2>/dev/null
    echo
    read -r -p "اضغط Enter للإغلاق (الخادم يكمل شغّال)..."
    exit 0
  fi
  echo "  ✗ المنفذ $PORT مشغول ببرنامج ثاني. أغلقه أو شغّل \"إيقاف رزق.command\" أولًا."
  echo; read -r -p "اضغط Enter للإغلاق..."; exit 1
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo
echo "----------------------------------------------------------------"
echo "   على هذا الجهاز:    $URL"
[ -n "$IP" ] && echo "   من الآيباد/الجوال:  http://$IP:$PORT/pos/   (نفس الواي فاي)"
echo "----------------------------------------------------------------"
echo
echo "   المساعد الذكي يطلب رقمك السري أول مرة — هذا طبيعي:"
echo "   الصلاحيات تُتحقق في الخادم، والمدير فقط يقدر يعتمد الإصلاحات."
echo
echo "   للإيقاف: اضغط Ctrl+C أو أغلق هذه النافذة."
echo "================================================================"
echo

( sleep 2 && open "$URL" 2>/dev/null ) &
exec python3 -m rizq_agent web --port "$PORT"

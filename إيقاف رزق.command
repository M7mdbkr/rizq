#!/bin/bash
# رزق — إيقاف الخادم المحلي.
# قاعدة بيانات الكاشير محفوظة في المتصفح ولا تتأثر بالإيقاف إطلاقًا.

printf '\033]0;إيقاف رزق\007'
clear
echo "================================================================"
echo "   إيقاف رزق"
echo "================================================================"
echo

STOPPED=0
for PAT in "rizq_agent web" "http.server 4173"; do
  PIDS=$(pgrep -f "$PAT" 2>/dev/null)
  if [ -n "$PIDS" ]; then
    echo "  إيقاف: $PAT  (pid: $(echo "$PIDS" | tr '\n' ' '))"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null
    STOPPED=1
  fi
done

if [ "$STOPPED" -eq 0 ]; then
  echo "  ما فيه خادم رزق شغّال."
else
  sleep 2
  LEFT=$(pgrep -f "rizq_agent web" 2>/dev/null)
  if [ -n "$LEFT" ]; then
    echo "  ما وقف بالطريقة العادية، نجبره..."
    # shellcheck disable=SC2086
    kill -9 $LEFT 2>/dev/null
    sleep 1
  fi
  echo
  echo "  ✓ تم الإيقاف. بيانات الكاشير محفوظة في المتصفح كما هي."
fi

echo
read -r -p "اضغط Enter للإغلاق..."

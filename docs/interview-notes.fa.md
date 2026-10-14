# یادداشت‌های مصاحبه: پروژهٔ EdgeShield

این فایل برای مرور قبل از مصاحبه نوشته شده. برای هر بخش سه چیز داره: **چی ساختیم**، **چرا اینطوری**، و **سؤال‌هایی که احتمالاً می‌پرسن** با جواب کوتاه. اسم فایل‌ها و مسیرها واقعی‌ان تا بتونی توی کد نشونشون بدی.

## ۰. چطور پروژه رو معرفی کنم (۳۰ ثانیه)

> «EdgeShield یک control plane برای یک CDN ساده‌ست. کاربر دامنه و origin ثبت می‌کنه، policy های cache و rate limit تعریف می‌کنه و cache رو purge می‌کنه. بک‌اند Django و DRF با MySQL و Redis و Celery هست، فرانت Vue 3. چون data plane واقعی نداریم، policy ها رو با شبیه‌ساز اجرا می‌کنم، ولی خود الگوریتم‌ها (LRU، token bucket، sliding window) پیاده‌سازی واقعی ان، با Lua روی Redis و تست همزمانی.»

**نکتهٔ صداقت:** اگه پرسیدن «ترافیک واقعی رو هم پروکسی می‌کنه؟» جواب: نه، فقط control plane ـه، و تفاوت control plane و data plane رو می‌تونم توضیح بدم. این نقطهٔ ضعف نیست؛ نشون می‌ده مرز رو می‌فهمی.

### نقشه به نقشه‌راه ۸ روزه

| روز | موضوع | کجای پروژه |
|---|---|---|
| ۰ | مسیر درخواست، REST، idempotency، stateless، JWT | health، auth، purge، nginx |
| ۴ | ORM: N+1، index، transaction، migration، JWT | `domains/views.py`، `domains/models.py`، `accounts` |
| ۵ | Redis و Celery، cache، retry، idempotency | `domains/tasks.py`، `edge/purge.py` |
| ۶ | ساختار داده و الگوریتم: LRU، rate limiter | `edge/lru.py`، `edge/ratelimit/` |
| ۷ | شبکه و CDN: HTTP، پراکسی، cache | `frontend/nginx.conf`، `edge/cache_sim.py` |
| ۸ | معماری و stateless، scale، نقاط شکست | بخش «معماری» پایین و «ریسک‌ها» |

---

## ۱. مسیر درخواست و health check (Slice 0)

**ساختیم:** `/api/health/live/` (فقط می‌گه پروسه زنده‌ست) و `/api/health/` (با `SELECT 1` دیتابیس رو هم چک می‌کنه)، تنظیمات از env، Docker Compose با healthcheck.

**مسیر کامل یک درخواست** (`GET /api/domains/`):
مرورگر ← (DNS) ← nginx ← gunicorn ← WSGI ← Django `HttpRequest` ← middlewareها (رفت) ← URL resolver ← view ← ORM ← MySQL ← serializer ← `Response` ← middlewareها (برگشت) ← gunicorn ← nginx ← مرورگر. نکته: middlewareها **دو بار** اجرا می‌شن، یک بار رفت و یک بار برگشت.

**سؤال‌ها:**
- *liveness و readiness چه فرقی دارن؟* liveness می‌گه پروسه گیر نکرده و شکستش یعنی restart. readiness می‌گه الان می‌تونم درخواست واقعی جواب بدم (دیتابیس وصله) و شکستش یعنی ترافیک نفرست، نه restart.
- *اگه liveness هم دیتابیس رو چک می‌کرد؟* با افتادن دیتابیس همهٔ instance ها یکجا restart می‌شدن. restart مشکل دیتابیس رو حل نمی‌کنه و فقط سرویس رو کاملاً از دسترس خارج می‌کنه و بعدش هجوم همزمان به دیتابیس میاد.
- *چرا `runserver` توی production نه؟* تک‌thread/غیرایمن و فقط برای توسعه‌ست. gunicorn چند worker پروسه‌ای، timeout و مدیریت سیگنال داره.
- *چرا secret توی env؟* هر چیزی که توی git باشه لو رفته حساب می‌شه. `settings.py` اگه `DJANGO_SECRET_KEY` نباشه عمداً fail می‌کنه.
- *چرا `manage.py check --deploy`؟* چک‌لیست رسمی امنیتی Django (HSTS، کوکی secure، SSL redirect). توی CI با `--fail-level WARNING` اجرا می‌شه.

**CORS (دقیق):** origin = scheme + host + port. مرورگر، نه سرور، جلوی خوندن پاسخ از origin دیگه رو می‌گیره مگه اینکه سرور با `Access-Control-Allow-Origin` اجازه بده. برای درخواست «غیرساده» (مثلاً هدر `Authorization`) اول یک `OPTIONS` (preflight) می‌فرسته؛ اگه جواب مثبت نباشه درخواست اصلی اصلاً فرستاده نمی‌شه. `CorsMiddleware` بالای `CommonMiddleware` ـه چون CommonMiddleware ممکنه خودش پاسخ بسازه (مثل ریدایرکت `/`) و بدون هدر CORS برگرده. توی dev با پراکسی Vite و توی prod با nginx فقط یک origin هست و CORS اصلاً فعال نمی‌شه. `curl` هیچ‌وقت با CORS مشکل نداره چون محافظت از کاربره، نه از سرور.

---

## ۲. احراز هویت و JWT (Slice 1)

**ساختیم:** `accounts/` با `User(AbstractUser)` و ورود با email یکتا، `register/login/refresh/me`، throttle روی login و register، فرانت با access token در حافظه (Pinia) و refresh در `localStorage`.

**JWT از داخل:** سه بخش `header.payload.signature`، هرکدوم base64url. payload **رمزنگاری نیست**، فقط امضا شده؛ هر کسی می‌تونه بخونه ولی بدون `SECRET_KEY` نمی‌تونه عوضش کنه چون امضای HMAC-SHA256 نامعتبر می‌شه. توی payload ما: `token_type`، `exp`، `iat`، `jti`، `user_id`. هیچ‌وقت رمز یا اطلاعات حساس نمی‌ذاریم.

**سؤال‌ها:**
- *stateless یعنی چی و چرا مهمه؟* سرور برای هر کاربر چیزی ذخیره نمی‌کنه؛ هر درخواست خودش اعتبارش رو با امضا ثابت می‌کنه. هر instance می‌تونه هر درخواستی رو جواب بده، پس scale افقی بدون sticky session یا session store مشترک ممکنه.
- *چرا access کوتاه (۱۵ دقیقه) و refresh بلند (۱ روز)؟* access توکنی‌ه که با هر درخواست می‌ره و بیشتر در معرض دزدیده شدنه. چون stateless ـه نمی‌شه فوراً باطلش کرد، پس عمر کوتاه ریسک رو محدود می‌کنه. refresh کم‌استفاده‌ست و فقط به یک endpoint می‌ره.
- *logout توی JWT یعنی چی؟* توی پیاده‌سازی ما فقط توکن‌ها از کلاینت پاک می‌شن. refresh token سمت سرور هنوز تا انقضاش معتبره. **محدودیت شناخته‌شده**؛ راه‌حل: blacklist یا rotation (در roadmap).
- *اگه access token دزدیده بشه؟* تا ۱۵ دقیقه معتبره. دلیل اینکه access رو توی حافظه نگه می‌داریم (نه `localStorage`) اینه که XSS نتونه راحت بخونتش.
- *`localStorage` در برابر کوکی HttpOnly؟* `localStorage` با XSS خونده می‌شه ولی CSRF نداره. کوکی HttpOnly با XSS خونده نمی‌شه ولی CSRF لازم داره (SameSite و توکن). ما برای سادگی refresh رو توی `localStorage` گذاشتیم و می‌دونیم کوکی HttpOnly امن‌تره.
- *رمز چطور ذخیره می‌شه؟* `PBKDF2` با salt و iteration بالا (پیش‌فرض Django). عمداً کند است تا brute force گرون بشه. توی تست‌ها فقط با hasher سریع جایگزینش می‌کنیم (`conftest.py`).
- *۴۰۱ در برابر ۴۰۳؟* ۴۰۱: هویتت معلوم نیست (توکن نیست یا نامعتبره). ۴۰۳: معلومی ولی اجازه نداری. ما برای دسترسی به آبجکت دیگران ۴۰۴ می‌دیم تا وجودش لو نره.
- *ایمیل تکراری و race condition؟* بررسی serializer کافی نیست، چون دو درخواست همزمان هر دو از بررسی رد می‌شن. `UNIQUE` توی دیتابیس تصمیم‌گیرندهٔ نهایی‌ه؛ `IntegrityError` رو به ۴۰۰ تبدیل می‌کنیم. این همون «I» و «C» در ACID.
- *چرا custom user از اول؟* بعد از اولین migrate عوض کردن `AUTH_USER_MODEL` خیلی دردسر داره.
- *brute force روی login؟* `ScopedRateThrottle` با Redis: ۱۰ تلاش در دقیقه به ازای IP. چون پشت nginx هستیم `TRUSTED_PROXY_COUNT=1` تا IP واقعی از `X-Forwarded-For` خونده بشه و جعلش ممکن نباشه (آخرین ورودی رو می‌خونه).

---

## ۳. ORM، دیتابیس و API (Slice 2 و 3)

**ساختیم:** `Domain` و `Origin` با ownership، policy های singleton، pagination، validation.

**سؤال‌ها:**
- *N+1 چیه و چطور حلش کردی؟* لیست دامنه‌ها بدون مراقبت یک query برای لیست و یک query به ازای هر دامنه برای شمردن origin ها می‌زنه. تعداد origin ها رو با یک **subquery همبسته** توی همون SQL می‌گیریم؛ برای جزئیات با `prefetch_related('origins')` (دو query ثابت).
- *اگه فقط تعداد query مهم باشه کافیه؟* نه. اول با `annotate(Count('origins'))` حل کردم و تعداد query ثابت بود، ولی با `EXPLAIN` روی ۱۰ هزار ردیف دیدم MySQL به‌خاطر JOIN و GROUP BY جدول موقت می‌سازه و **قبل از LIMIT** مرتب می‌کنه (~۳۴ms). با subquery و ایندکس `(owner, created_at DESC, id DESC)` به ~۱.۵ms رسید و `Extra` در `EXPLAIN` خالی شد (بدون filesort). درس: تعداد query معیار کافی نیست؛ پلن اجرا رو هم ببین. **تست می‌شه:** `test_domain_list_query_count_is_constant` تعداد query برای ۲ و ۱۷ ردیف رو مقایسه می‌کنه. من عمداً N+1 رو برگردوندم و تست fail شد.
- *`select_related` در برابر `prefetch_related`؟* `select_related` با JOIN برای FK/OneToOne (یک query). `prefetch_related` یک query جدا و join توی پایتون برای رابطه‌های یک‌به‌چند و چندبه‌چند.
- *چرا pagination پایدار نیازمند ترتیب یکتاست؟* بدون `ORDER BY` یکتا (`-created_at, -id`) ردیف‌ها بین صفحه‌ها ممکنه جابه‌جا یا تکراری بشن. `annotate` با GROUP BY ترتیب `Meta` رو نادیده می‌گیره، پس صریحاً `order_by` می‌زنیم.
- *ایندکس‌ها؟* `Index(owner, -created_at)` برای «دامنه‌های من به ترتیب جدیدترین»؛ `UNIQUE` روی `name`؛ `UNIQUE(domain, host, port)` روی origin؛ `UNIQUE(domain, idempotency_key)` روی purge. ایندکس ترکیبی ترتیب ستون‌ها مهمه (اول فیلتر برابری، بعد ترتیب). نکتهٔ مهم: اگه `ORDER BY created_at DESC, id DESC` داری ولی ایندکس `(owner, created_at DESC)` باشه، tie-breaker یعنی `id` توی ایندکس نیست و MySQL filesort می‌کنه؛ پس `id` هم باید داخل ایندکس بیاد (MySQL 8 ایندکس نزولی رو پشتیبانی می‌کنه).
- *چرا ownership با فیلتر queryset و ۴۰۴؟* خطای دسترسی نشت وجود شیء رو نمی‌ده. فیلتر توی `get_queryset` یعنی هیچ view ای فراموش نمی‌کنه.
- *چرا نام دامنه سراسری یکتاست؟* دو حساب نباید یک hostname رو مدیریت کنن. نکتهٔ امنیتی: این یعنی کسی می‌تونه از پیام خطا بفهمه دامنه‌ای قبلاً ثبت شده (enumeration). توی سیستم واقعی تأیید مالکیت با DNS TXT لازمه.
- *چرا policy ها `OneToOne` با `get_or_create`؟* دقیقاً یک policy برای هر دامنه، بدون حالت «policy ندارم». نکته: GET ای که می‌نویسه؛ تصمیم آگاهانه، ولی جایگزینش ساختن موقع ساخت دامنه‌ست.
- *چرا PATCH روی مقدار ترکیبی اعتبارسنجی می‌شه؟* قانون «burst فقط برای token bucket» به فیلدهایی که نیومدن هم بستگی داره؛ پس مقدار ذخیره‌شده + ورودی رو با هم قضاوت می‌کنیم.
- *`/config/` برای چیه؟* snapshot همهٔ چیزی که data plane لازم داره، با `version`. این مرز control plane و data plane‌ه: control plane تنظیم می‌سازه، data plane اون رو می‌خونه و اجرا می‌کنه.
- *سؤال transaction:* جایی که چندین نوشتن باید با هم اتفاق بیفتن (ساخت purge job و ثبت on_commit) داخل `transaction.atomic()` ـه.

---

## ۴. ساختار داده و الگوریتم (Slice 4)

**ساختیم:** `edge/lru.py`، `edge/ratelimit/` (memory و Redis)، `edge/cache_sim.py`.

**LRU:** hash map برای پیدا کردن O(1) + doubly linked list برای ترتیب استفاده (سر لیست = تازه‌ترین، دم = قربانی بعدی). get و put هر دو O(1) چون با داشتن نود می‌شه اون رو از وسط لیست در O(1) جدا کرد. نودهای sentinel حالت‌های خاص (لیست خالی/یک‌عضوی) رو حذف می‌کنن. thread-safe با lock.
- *چرا `OrderedDict` ساده‌تره و چرا لینک‌لیست رو نوشتی؟* برای مصاحبه نشون می‌ده مکانیزم رو می‌فهمم؛ توی production `OrderedDict.move_to_end` کافیه.

**Rate limiting:**
| الگوریتم | ایده | حافظه | رفتار |
|---|---|---|---|
| Fixed window | شمارنده در پنجرهٔ ثابت | O(1) | ضعف: در مرز پنجره می‌شه دو برابر burst داد |
| **Sliding window log** (پیاده‌سازی ما) | زمان هر درخواست ذخیره، شمردن در N ثانیهٔ اخیر | O(limit) | دقیق، گرون‌تر |
| Sliding window counter | تقریبی با دو پنجره | O(1) | تقریبی، ارزان |
| **Token bucket** (پیاده‌سازی ما) | سطل با ظرفیت، پر شدن با نرخ ثابت | O(1) | اجازهٔ burst تا ظرفیت، میانگین نرخ رو حفظ می‌کنه |

- *چرا Lua روی Redis؟* خواندن، محاسبه و نوشتن باید **اتمیک** باشن. اگه از پایتون بخونیم و بنویسیم، دو درخواست همزمان وضعیت یکسان می‌بینن و هر دو مجاز می‌شن. اسکریپت Lua داخل Redis تک‌رشته‌ای اجرا می‌شه. **اثبات:** تست با ۱۰ thread و ۲۰۰ درخواست همزمان دقیقاً `limit` تا رد می‌کنه؛ نسخهٔ ساده توی آزمایشم ۳۰ تا رد کرد با limit ۲۵.
- *چرا زمان از بیرون تزریق می‌شه؟* تست رفتار زمانی بدون `sleep` (سریع و قطعی). تصمیم طراحی: زمان رو پایتون می‌ده؛ اگه چند سرور ساعت‌های متفاوت داشته باشن بهتره از `TIME` خود Redis استفاده بشه. ما ساعت عقب‌رو رو با `max(0, now - ts)` تحمل می‌کنیم تا توکن مفت ساخته نشه.
- *key های بی‌استفاده؟* `EXPIRE` ست می‌شه (دو برابر زمان پر شدن کامل) تا کلاینت‌های idle زباله نسازن. نسخهٔ in-memory با LRU محدود می‌شه تا چرخاندن IP حافظه رو نبره.
- *وقتی Redis بیفته؟* sandbox ما ۵۰۳ می‌ده (fail closed). یک data plane واقعی معمولاً **fail open** می‌کنه (ترافیک رو رد نمی‌کنه) چون نباید خرابی limiter کل سایت رو ببنده. این تصمیم محصولیه، باید آگاهانه گرفته بشه.
- *چرا الگوریتم جزو نام key ـه؟* token bucket یک hash و sliding window یک sorted set ذخیره می‌کنه؛ عوض کردن الگوریتم روی یک key باعث `WRONGTYPE` می‌شد.
- *per-IP یا global؟* per-IP از یک کلاینت پرزور محافظت می‌کنه ولی IPهای زیاد (botnet) رو نه؛ global کل دامنه رو محافظت می‌کنه ولی یک کاربر بد بقیه رو هم می‌بنده.

**Cache simulator:** خروجی `HIT/MISS/STALE/BYPASS`. `STALE` یعنی منقضی شده ولی طبق `serve_stale` فوراً جواب داده می‌شه و پشت‌صحنه refresh می‌شه (**stale-while-revalidate**): تأخیر کم می‌شه ولی تعداد درخواست به origin کم نمی‌شه. bypass path ها segment-aware ان (`/admin` با `/administrator` match نمی‌شه). کلید cache با query string یا بدون اون.

---

## ۵. Celery، Redis و پردازش غیرهمزمان (Slice 5)

**ساختیم:** `POST /api/domains/{id}/purges/` ← ۲۰۲ ← worker ← وضعیت job توی DB.

**جریان:** (۱) job با وضعیت `pending` ذخیره می‌شه، (۲) **بعد از commit** پیام به broker می‌ره، (۳) worker اون رو می‌گیره، (۴) وضعیت `running/retrying/succeeded/failed` توی MySQL به‌روز می‌شه، (۵) کلاینت با GET وضعیت رو poll می‌کنه.

**سؤال‌ها:**
- *چرا ۲۰۲ و نه ۲۰۰؟* کار هنوز انجام نشده؛ فقط پذیرفته شده. هدر `Location` به منبع پیگیری اشاره می‌کنه.
- *چرا `transaction.on_commit`؟* اگه پیام قبل از commit بره، worker سریع‌تر از commit می‌رسه و ردیف رو نمی‌بینه. enqueue فقط بعد از commit.
- *idempotency چیه و چطور پیاده شد؟* کلاینت می‌تونه درخواست رو امن تکرار کنه (timeout، دابل کلیک). هدر `Idempotency-Key`: همون کلید و همون بدنه ← job اصلی دوباره (۲۰۰ + `Idempotent-Replayed`)؛ همون کلید با بدنهٔ دیگه ← ۴۰۹. بدنه با hash مقایسه می‌شه (مرتب و بدون تکرار نرمال شده). برنده شدن در race با `UNIQUE(domain, idempotency_key)` تضمین می‌شه.
- *`acks_late` یعنی چی؟* پیام فقط **بعد از اتمام** task تأیید می‌شه؛ اگه worker وسط کار بمیره پیام دوباره تحویل می‌شه (**at-least-once**). نتیجه: task باید **idempotent** باشه. purge ما هست: حذف چیزی که قبلاً حذف شده ۰ می‌شمره و حالت نهایی یکسانه. وقتی job قبلاً `succeeded` باشه task هیچ کاری نمی‌کنه.
- *retry چطور؟* فقط خطای **موقتی** (Redis) با backoff نمایی و **jitter** (۲، ۴، ۸... ثانیه تا سقف ۶۰) تا ۵ بار. jitter باعث می‌شه وقتی همه با هم fail شدن همزمان به سرویس در حال بهبود هجوم نیارن. خطای **دائمی** (باگ) بدون retry `failed` می‌شه چون تکرارش فایده نداره.
- *چرا وضعیت job توی DB و نه Celery result backend؟* DB منبع حقیقت و قابل پرس‌وجوست و Redis فقط صف موقتی‌ه.
- *prefetch_multiplier=1؟* worker پیام اضافه انبار نمی‌کنه که worker آزاد دیگه‌ای بتونه بگیره (برای taskهای طولانی/نابرابر).
- *`visibility_timeout`؟* مدتی که broker منتظر ack می‌مونه قبل از تحویل مجدد؛ باید از طولانی‌ترین task بیشتر باشه وگرنه یک task دو بار اجرا می‌شه.
- *back-pressure؟* بیش از ۵ job فعال برای یک دامنه ← ۴۲۹ با `Retry-After`. جلوی انبار شدن صف رو می‌گیره.
- *چرا `SCAN` و `UNLINK` نه `KEYS` و `DEL`؟* `KEYS` کل Redis رو برای همه بلاک می‌کنه. `SCAN` تدریجی و `UNLINK` حذف غیرهمزمان حافظه‌ست. کاراکترهای glob داخل داده escape می‌شن (`/weird[1]`).
- *اگه broker قطع باشه؟* job `failed` با پیام `enqueue failed` علامت می‌خوره و کلاینت ۵۰۳ می‌گیره. (بهبود ممکن: الگوی outbox و یک reaper.)

---

## ۶. استقرار و عملیات (Slice 6)

**ساختیم:** Dockerfile غیرroot، compose با `migrate` یک‌بارمصرف، nginx، WhiteNoise، CI.

- *چرا migration داخل سرویس جدا؟* اگه هر replica موقع بالا اومدن migrate بزنه، با هم race می‌کنن. یک job یک‌بارمصرف که بقیه با `service_completed_successfully` منتظرش می‌مونن.
- *چرا کاربر غیرroot؟* اگه پروسه نفوذ بشه، توی کانتینر root نیست.
- *چرا multi-stage برای فرانت؟* Node فقط برای build لازمه؛ imageی نهایی فقط nginx و فایل‌های ثابته (کوچک‌تر و سطح حملهٔ کمتر).
- *nginx چه می‌کنه؟* فایل‌های Vue رو سرو می‌کنه، `/api` و `/admin` رو به gunicorn پراکسی می‌کنه، هدرهای `X-Forwarded-*` رو می‌ذاره، برای مسیرهای SPA به `index.html` برمی‌گرده، و asset های hash‌دار رو یک سال cache می‌کنه (`index.html` هیچ‌وقت). **نکتهٔ مهم nginx:** `add_header` توی یک `location` هدرهای سطح `server` رو **بازنویسی** می‌کنه، به همین خاطر توی location ها فقط `expires` استفاده کردیم.
- *چرا WhiteNoise؟* فایل‌های static ادمین رو مستقیم از gunicorn می‌ده و نیازی به volume مشترک با nginx نیست.
- *چرا `DJANGO_USE_HTTPS`؟* SSL redirect، کوکی secure و HSTS فقط روی HTTPS معنی دارن. روی localhost با HTTP ساده باید خاموش بشن (وگرنه ادمین کار نمی‌کنه) ولی روی دامنهٔ واقعی پیش‌فرض روشنه و `check --deploy` بدون هشدار پاس می‌شه. مسیر `/api/health/` از redirect مستثنی‌ست تا probe لود بالانسر روی HTTP ساده کار کنه.
- *CI چی می‌سنجه؟* lint، `check --deploy`، sync بودن migration ها (`makemigrations --check`)، تست روی **MySQL 8 و Redis واقعی**، build فرانت و build ایمیج‌ها.

---

## ۷. معماری، scale و نقاط شکست (روز ۸ نقشه‌راه)

- **stateless بودن:** سرور API هیچ stateای توی حافظه نداره (JWT، Redis، MySQL)، پس با افزودن instance پشت load balancer scale می‌شه. استثنا: LRU in-memory شبیه‌ساز نمونه‌ایه که عمداً stateless نگه داشتیم (هر درخواست خودش یک LRU می‌سازه).
- **گلوگاه‌ها:** MySQL (نوشتن)، Redis (single instance)، worker (تعداد concurrency). خواندن‌ها با read replica و cache قابل scale‌ان؛ rate limiter با sharding کلیدها.
- **اگه Redis بیفته:** sandbox و purge ۵۰۳ می‌دن؛ CRUD عادی کار می‌کنه چون به Redis وابسته نیست (برای همین readiness فقط DB رو چک می‌کنه). throttle لاگین **fail open** ـه (`core/throttling.py`): اگه کش در دسترس نباشه خطا لاگ می‌شه و درخواست رد می‌شه، تا خرابی Redis ورود کاربران رو قطع نکنه. این یک trade-off آگاهانه‌ست؛ محصول سخت‌گیرتر می‌تونه fail closed کنه.
- **اگه worker بمیره:** پیام‌ها تا زنده شدن worker می‌مونن؛ taskهای نیمه‌کاره redeliver می‌شن (idempotent).
- **اگه دیتابیس بیفته:** readiness ۵۰۳ می‌ده، load balancer ترافیک رو می‌بره، liveness هنوز اوکی‌ه و کانتینر restart نمی‌شه.
- **مشاهده‌پذیری:** فعلاً لاگ stdout و health. گام بعد: متریک (Prometheus)، trace، و شمارش‌گر hit/miss.

## ۸. محدودیت‌ها (خودت قبل از اینکه بپرسن بگو)

1. data plane واقعی نیست؛ policy ها فقط با شبیه‌ساز اجرا می‌شن.
2. logout سمت سرور اثر نداره (refresh blacklist/rotation ندارم).
3. تأیید مالکیت دامنه (DNS) و تأیید ایمیل/بازیابی رمز نیست.
4. job ای که broker وقت enqueue خراب باشه فقط `failed` می‌شه (outbox/reaper ندارم).
5. rate limiter فعلاً فقط sandbox ـه و روی ترافیک واقعی اعمال نمی‌شه.

## ۹. جمله‌های آمادهٔ رزومه (انگلیسی)

- Built **EdgeShield**, a CDN control-plane API (Django 5 LTS, DRF, MySQL 8, Redis, Celery) with a Vue 3 dashboard; 200+ automated tests, CI on MySQL and Redis.
- Implemented **atomic token-bucket and sliding-window rate limiters** as Redis Lua scripts and verified exactly-N admission under 200 concurrent requests; built an O(1) LRU cache and a cache-policy simulator.
- Designed **asynchronous purge jobs** with Celery: idempotency keys, `on_commit` enqueueing, `acks_late`, exponential backoff with jitter, transient vs. permanent failure handling.
- Eliminated **N+1 queries** (annotate/prefetch) with query-count regression tests; enforced multi-tenant isolation via scoped querysets and database constraints.
- Containerised with non-root images, one-shot migrations, health checks, nginx reverse proxy and hardened Django settings (`check --deploy` clean).

## ۱۰. دمو ۵ دقیقه‌ای برای مصاحبه

1. `make prod-up` و باز کردن `localhost:8080` ← «اینجا nginx جلوی gunicorn ـه».
2. ثبت‌نام، ساخت دامنه و origin ← «ownership و ۴۰۴ برای دیگران».
3. فعال کردن rate limit ۵ در ۳۶۰۰ ثانیه و sandbox با ۱۰ درخواست ← «Lua و اتمیک بودن».
4. simulator cache با مثال پیش‌فرض ← «LRU و stale-while-revalidate».
5. purge با `/blog/*` و دابل‌کلیک ← «idempotency، ۲۰۲ و worker». `docker compose logs worker` رو نشون بده.
6. اگه وقت بود: `docker compose stop redis` ← «sandbox ۵۰۳ می‌ده ولی CRUD کار می‌کنه، چرا؟ readiness».

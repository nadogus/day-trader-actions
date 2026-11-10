# GitHub Actions altyapısı

Bu klasör, orijinal Day Trader Skills araçlarını
([Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill))
Claude, Claude Code veya herhangi bir yapay zekâ API'si olmadan GitHub
Actions üzerinde Python 3.13 ile çalıştırır. Kökteki 11 `trader-*` klasörü,
`tests/`, `README.md`, `LICENSE` ve `.claude-plugin/` orijinalden
değiştirilmeden kopyalandı. Tek ek `.github/` klasörüdür.

## Otomatik zamanlamalar (America/New_York, yaz/kış saati GitHub tarafından)

| Workflow | Zaman | Orijinal komut | Kaynak |
| --- | --- | --- | --- |
| `universe.yml` | Pazartesi 07:00 | `scan.py universe` | scanner SKILL.md: "Once a week or so" |
| `daily.yml` | Pzt-Cum 08:00 | `scan.py daily`, sonra `news.py`, sonra `facts.py <watchlist sembolleri>` | scanner: "Before the open"; news: "each morning before the open"; analyst: "the tickers on trader/watchlist.json" |
| `orb.yml` | Pzt-Cum 10:15 | `scan.py orb --at 10:15`, sonra `news.py` | scanner: "10:15 is the classic"; README akışı scanner -> news |
| `pelosi.yml` | Pzt-Cum her saat :05 | `ptr.py new --member pelosi --read` | pelosi SKILL.md: "every hour on weekdays is plenty" |
| `shadow.yml` | Pzt-Cum 16:20 | gölge takip: 10:15 ORB listesine orijinal `entry.py check` ve `exit.py plan`, sonra günün gerçek 5 dakikalık mumları | sanal, broker yok |
| `tests.yml` | her push | `python -m unittest discover -s tests -v` | README |
| `run.yml` | elle | seçilen aracı yazılan argümanlarla | |

`daily` ve `orb` hafta sonu ve NYSE tatillerinde (tarayıcının kendi takvimi)
hiçbir şey çalıştırmaz ve bunu not olarak yazar. ORB'nin kesim saati GitHub
geç başlasa bile 10:15'tir (`--at 10:15`).

## Otomatik çalışmayanlar ve nedenleri

- `trader-entry`, `trader-exit`, `trader-timemachine`: orijinal akışta
  kullanıcı bir grafiği kendisi seçer ("Never pick for them"). Otomasyon hisse
  seçmez. `run.yml` ile elle çalıştırılabilir.
- `trader-budget`, `trader-risk`: kullanıcının kendi limitlerini
  (`trader/config.json`) ve bir işlem planını ister. Public repoya kişisel
  finans bilgisi konmadığı için otomatik çalışmaz.
- `trader-alerts`: kullanıcının kuracağı alarm kurallarını ve açık bir planı
  ister.
- `trader-broker`: emir gönderir. "Never places orders from a scheduled or
  background task." Hiçbir workflow çağırmaz, `run.yml` listesinde de yoktur.

## Sonuçlar

- Araçlar `results` dalındaki `trader/` klasöründe çalışır. Orijinal akıştaki
  gibi her araç bir öncekinin yazdığını okur.
- Her çalışma `runs/<New York tarihi>/<saat>-<araç>/` altına `run.json`
  (komut, çıkış kodu, süre, stdout, stderr), `stdout.txt`, `stderr.txt` ve
  değişen `trader/` dosyalarının kopyası olarak yazılır. Ayrıca 90 gün
  artifact olarak saklanır.
- Bir araç başarısız olursa iş kırmızı biter. Tarama başarısızsa sonraki
  adımlar eski dosyayı okumaz, "SKIPPED" olarak raporlanır.

## SEC_USER_AGENT

Settings > Secrets and variables > Actions > New repository secret:
`SEC_USER_AGENT` = "Ad Soyad e-posta". Yoksa `trader-analyst` adımı açık bir
uyarıyla bekler. `trader-news` orijinal davranışıyla SEC kısmını atlar ve
bunu yazar. Diğer adımlar çalışmaya devam eder. Secret hiçbir yere yazdırılmaz.

## Gölge takip (shadow.yml)

Para, broker ve emir yok. Her işlem gününün kapanışından sonra 10:15 ORB
listesindeki her hisse için orijinal `trader-entry` (giriş oldu mu, nerede) ve
`trader-exit` (stop = açılış aralığı ortası, hedef = 2R) çalışır. Sonra günün
gerçek 5 dakikalık mumlarında önce stop mu hedef mi geldiğine bakılır:

- Aynı mumda ikisi birden: stop sayılır (kötümser varsayım).
- Stop ya da hedefin ötesine boşlukla açılış: o mumun açılışından çıkılır.
- Hiçbiri: 15:55'te kapanır.
- Taraf başı %0.05 maliyet düşülür.

Sonuçlar `results` dalında: `shadow/trades.csv` (her hisse bir satır) ve
`shadow/summary.md` (kazanma oranı, ortalama R, toplam R, en kötü düşüş; ayrıca
her günün ilk 3 sinyali). Geçmiş bir gün elle yeniden çalıştırılabilir
(Actions > Shadow test > Run workflow > tarih).

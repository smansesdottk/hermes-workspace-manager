# 📖 DOKUMENTASI LENGKAP: HERMES WORKSPACE MANAGER

> **Versi Dokumen**: 1.0.0  
> **Target Arsitektur**: Multi-Tenant Docker Orchestrator untuk Hermes Agent & 9Router  
> **Stack Teknologi**: Python 3.10+, Flask, SQLite (WAL Mode), Docker & Docker Compose, xterm.js, Gunicorn/Waitress, Gevent, Cloudflare Zero Trust.

---

## 📑 DAFTAR ISI
1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Arsitektur Sistem & Topologi Jaringan](#2-arsitektur-sistem--topologi-jaringan)
3. [Fitur-Fitur Utama & Rincian Cara Kerja](#3-fitur-fitur-utama--rincian-cara-kerja)
4. [Struktur Database & Penyimpanan Data](#4-struktur-database--penyimpanan-data)
5. [Daftar Endpoint & Rute API Web](#5-daftar-endpoint--rute-api-web)
6. [Sistem Keamanan & Hardening](#6-sistem-keamanan--hardening)
7. [Analisis SWOT: Kelebihan & Kekurangan](#7-analisis-swot-kelebihan--kekurangan)
8. [Peluang & Rencana Pengembangan Masa Depan (Roadmap)](#8-peluang--rencana-pengembangan-masa-depan-roadmap)

---

## 1. RINGKASAN EKSEKUTIF

**Hermes Workspace Manager** (di-branding sebagai *MyHermes*) adalah platform kontrol berbasis web yang mengotomatisasi penyediaan (*provisioning*), isolasi keamanan, manajemen paket masa aktif (*subscription*), antarmuka komunikasi (WhatsApp/Telegram), terminal interaktif, dan pemeliharaan untuk asisten kecerdasan buatan otonom **Hermes Agent** yang dipadukan dengan **9Router Universal Gateway**.

Aplikasi ini mengubah konfigurasi multi-kontainer Docker yang rumit menjadi portal *Self-Service SaaS* yang siap digunakan oleh pengguna umum maupun pengembang tanpa memerlukan pemahaman mendalam tentang CLI Linux atau arsitektur Docker.

---

## 2. ARSITEKTUR SISTEM & TOPOLOGI JARINGAN

### A. Diagram Alur Komunikasi
```
[ Pengguna Publik / Klien ]
       │ (HTTPS / Web Browser)
       ▼
[ Cloudflare Edge / Reverse Proxy ]
       │ (Cloudflare Tunnel: 127.0.0.1:8899)
       ▼
[ Host Server: Flask Panel (Gunicorn / Waitress) ]
       │
       ├──► Database SQLite (panel.db, WAL Mode, Busy Timeout 60s)
       ├──► Docker Daemon Socket (/var/run/docker.sock)
       │
       ▼ (Orkestrasi Multi-Instance)
┌─────────────────────────────────────────────────────────────┐
│ INSTANCE WORKSPACE: "dockerman-<slug>"                      │
│                                                             │
│  ┌─────────────────────────┐     ┌────────────────────────┐ │
│  │ Kontainer Hermes Agent  │ ──► │ Kontainer 9Router      │ │
│  │ (Port 9119 terisolasi)  │ ◄── │ (Port 20128 bridge)    │ │
│  └─────────────────────────┘     └────────────────────────┘ │
│               ▲                               │             │
│               │ (Eksekusi Lokal)              │ (API Out)   │
│       ┌───────────────┐                       ▼             │
│       │ Volume Data   │             [ LLM Cloud Providers ] │
│       │ /opt/data/    │             (OpenAI, Claude, Gemini,│
│       │ - cron/       │              Qwen, DeepSeek, Free)  │
│       │ - SOUL.md     │                                     │
│       │ - memori DB   │                                     │
│       └───────────────┘                                     │
└─────────────────────────────────────────────────────────────┘
```

### B. Isolasi Multi-Tenant
1. **Network Bridge Terdedikasi**: Setiap instance dibuatkan jaringan Docker terpisah `n_<slug>` (`bridge`). Kontainer instance A tidak dapat melihat atau berkomunikasi dengan kontainer instance B.
2. **Loopback Binding**: Seluruh port internal instance di-bind hanya ke `127.0.0.1:<port>` pada host, sehingga tidak ada port kontainer yang terbuka langsung ke internet publik.
3. **Resource Capping**: Admin dapat menentukan batas limit CPU (contoh: `1.0` core), batas memori RAM (contoh: `1536M`), dan kuota penyimpanan disk per instance.

---

## 3. FITUR-FITUR UTAMA & RINCIAN CARA KERJA

### 1. Sistem Autentikasi & Otorisasi Berjenjang
- **Role Administrator**:
  - Mengelola seluruh pengguna (Buat user, Reset password, Toggle aktif/nonaktif, Hapus akun beserta seluruh data).
  - Mengelola seluruh instance (Alokasi port, set CPU/RAM limit, ganti paket langganan, update kontainer, force destroy).
  - Background async seed builder untuk build image bibit lokal Hermes tanpa Cloudflare 524 timeout.
  - Editor Pricing CMS dinamis untuk 3 tier paket di landing page.
  - Audit Trail Log dengan zona waktu Makassar (WITA, UTC+8).
- **Role Pengguna (User)**:
  - Hanya dapat melihat dan mengelola instance miliknya sendiri.
  - Mengakses 4 sub-tab: Dasbor Utama, Terminal Instance, Tugas Terjadwal (Cron), dan Menu Setting.

### 2. Live Test Chat Model Aktif
- Antarmuka chat interaktif langsung di browser untuk menguji kecerdasan model yang sedang aktif.
- Mendukung pemformatan teks Markdown, blok kode dengan penyorotan sintaksis (*syntax highlighting*), tabel responsif, dan pembersihan karakter byte liar (*mojibake sanitizer*).
- Riwayat percakapan uji coba disimpan dalam sesi browser lokal agar pengguna dapat melihat konteks percakapan sebelumnya.

### 3. Integrasi WhatsApp & Telegram 1-Klik
- **WhatsApp Web Pairing**:
  - Mengambil QR string dari kontainer Hermes dan merendernya secara real-time menjadi gambar QR code SVG/PNG di browser.
  - Mendukung dua mode: **Self-Chat Mode** (Hermes merespon pesan yang dikirim ke nomor sendiri) dan **Bot Channel Mode** (Hermes merespon pesan dari orang lain).
  - Tombol **Putus & Bersihkan Sesi WhatsApp** untuk mereset kredensial autentikasi WhatsApp yang tersimpan jika terjadi kegagalan pairing.
- **Telegram Bot Pairing**:
  - Konfigurasi token bot Telegram melalui popup UI modal tanpa menyentuh file konfigurasi secara manual.

### 4. Web Terminal Interaktif (xterm.js + PTY)
- Berada pada sub-tab `/i/<slug>/terminal`.
- Menggunakan pustaka standar industri `xterm.js` dengan Addon Fit dan dukungan warna ANSI 256.
- Menjalankan shell `/bin/bash` di dalam kontainer Hermes dengan direktori kerja otomatis di `/opt/data`.
- Symlink global biner `/usr/local/bin/hermes` dan `/usr/bin/hermes` telah disuntikkan ke image kontainer sehingga perintah CLI `hermes` dapat langsung dipanggil.
- Terisolasi penuh (non-root execution / sandbox) dan memiliki fitur penyesuaian ukuran jendela dinamis (*window resize listener*).

### 5. GUI Visual Tugas Terjadwal (Cron Jobs)
- Mengakses dan memanipulasi file `cron/jobs.json` dan database eksekusi SQLite `cron/executions.db` di dalam direktori instance.
- **Fungsi**:
  - Menampilkan daftar pekerjaan otomatis, ekspresi jadwal (misal: `30m`, `every 2h`, `0 9 * * *`), target pengiriman (`origin`, `telegram`, `whatsapp`), dan status eksekusi terakhir.
  - Tombol **Jeda / Aktifkan** jadwal secara instan.
  - Tombol **Hapus Tugas**.
  - Form pembuatan jadwal baru dengan panduan format waktu yang ramah pemula.

### 6. Sistem Paket Berlangganan & Manajemen Masa Aktif
- Pilihan paket fleksibel: **Trial (1, 2, 3, 7 Hari)**, **1 Bulan**, **3 Bulan**, **6 Bulan**, **12 Bulan**, **Custom Bulan**, **Lifetime**, dan **⛔ Kunci / Langganan Habis**.
- **Auto-Stop & Lockdown Enforcement**:
  - Jika masa aktif instance habis, sistem otomatis menghentikan kontainer.
  - Tindakan Start/Restart diblokir oleh backend.
  - Akses user ke workspace instance dialihkan ke peringatan berlangganan habis.
  - Banner peringatan kuning muncul otomatis H-3 sebelum masa langganan berakhir.

### 7. Dual Engine 1-Click Update System
- Memeriksa SHA image kontainer yang sedang berjalan terhadap Image Master lokal:
  - Image Hermes: `local/hermes-agent-dockerman:latest`
  - Image 9Router: `decolua/9router:latest`
- Jika salah satu atau kedua image memiliki versi baru, tombol update akan muncul secara kondisional di dasbor user.
- Tombol Update mempertahankan seluruh data di `/opt/data` (volume tidak terhapus) dan hanya memperbarui layer aplikasi kontainer.

### 8. Cadangkan & Pulihkan (Backup & Restore .tar.gz)
- Mengompresi seluruh isi folder `/opt/data` milik instance menjadi arsip `.tar.gz` yang dapat diunduh langsung ke komputer lokal pengguna.
- Dilengkapi proteksi keamanan **Anti Zip-Slip Traversal** saat proses ekstraksi file restore.

---

## 4. STRUKTUR DATABASE & PENYIMPANAN DATA

Database SQLite disimpan di file `panel.db` dengan proteksi izin file `0600`.

### Tabel-Tabel Utama:
1. `users`:
   - `id`, `username`, `password_hash`, `role` (`admin`/`user`), `is_active`, `created_at`.
2. `instances`:
   - `id`, `slug`, `owner_id`, `hermes_port`, `router_port`, `status`, `created_at`, `expires_at`, `plan_duration`, `cpu_limit`, `ram_limit`, `disk_limit`.
3. `pricing_plans`:
   - `id`, `name`, `badge`, `base_price`, `promo_price`, `features_text`, `is_active`, `is_popular`, `stock_left`.
4. `audit_logs`:
   - `id`, `actor`, `action`, `target`, `details`, `ip_address`, `timestamp`.

### Struktur File Workspace di Disk:
```
instances/<slug>/
├── docker-compose.yml       # Definisi kontainer, network, limit, dan port
├── hermes-data/             # Volume mount kontainer Hermes (/opt/data)
│   ├── config.yaml          # Konfigurasi model aktif & provider
│   ├── SOUL.md              # Persona dan instruksi sistem agen AI
│   ├── cron/
│   │   ├── jobs.json        # Definisi tugas terjadwal
│   │   └── executions.db    # Riwayat eksekusi cron
│   └── whatsapp/            # Kredensial sesi WhatsApp Web Baileys
└── router-data/             # Volume mount kontainer 9Router (/root/.9router)
    └── config.json          # Provider keys, endpoint, dan routing map
```

---

## 5. DAFTAR ENDPOINT & RUTE API WEB

| Rute Endpoint | Metode | Role Izin | Deskripsi |
|---|---|---|---|
| `/` | GET | Publik | Halaman Landing Page Publik & Simulasi Dual-Pane |
| `/login` | GET, POST | Publik | Autentikasi Pengguna & Sesi |
| `/logout` | GET | Authenticated | Mengakhiri Sesi |
| `/dashboard` | GET | Authenticated | Dasbor Utama (Admin: Semua Instance; User: Instance Milik Sendiri) |
| `/account/password` | POST | Authenticated | Ganti Password Akun Sendiri |
| `/admin/users` | POST | Admin | Membuat Akun User Baru |
| `/admin/users/<uid>/toggle` | POST | Admin | Mengaktifkan / Menonaktifkan User |
| `/admin/users/<uid>/reset-password`| POST | Admin | Reset Password User Otomatis |
| `/admin/users/<uid>/delete` | POST | Admin | Menghapus User Beserta Seluruh Instance & Data Fisik |
| `/admin/instances` | POST | Admin | Membuat Instance Baru |
| `/admin/instances/<slug>/plan` | POST | Admin | Mengubah Masa Aktif / Paket Berlangganan |
| `/admin/instances/<slug>/limits` | POST | Admin | Mengubah Kuota CPU, RAM, dan Disk |
| `/admin/instances/<slug>/destroy`| POST | Admin | Menghapus Instance (+ opsi hapus data fisik di disk) |
| `/admin/instances/<slug>/update` | POST | Admin | Memperbarui Kontainer Instance Tertentu |
| `/admin/instances/update-all` | POST | Admin | Memperbarui Seluruh Kontainer Instance Aktif Sekaligus |
| `/admin/seeds/hermes/update` | POST | Admin | Memicu Background Async Rebuild Image Hermes Master |
| `/admin/seeds/9router/update`| POST | Admin | Menarik Image 9Router Terbaru dari Docker Hub |
| `/admin/seeds/status` | GET | Admin | Polling Status Build Image Master di Background |
| `/admin/seeds/versions` | GET | Admin | Pengecekan Hash Git Lokal vs Upstream GitHub |
| `/admin/pricing/<plan_id>` | POST | Admin | Memperbarui Informasi Paket Harga & Stok di Landing Page |
| `/i/<slug>` | GET | Owner/Admin | Halaman Dasbor Workspace Instance |
| `/i/<slug>/settings` | GET | Owner/Admin | Halaman Pengaturan Persona SOUL.md & Backup |
| `/i/<slug>/cron` | GET | Owner/Admin | Halaman GUI Manajemen Cron Jobs |
| `/i/<slug>/cron/create` | POST | Owner/Admin | Menambahkan Cron Job Baru |
| `/i/<slug>/cron/<jid>/toggle`| POST | Owner/Admin | Jeda / Aktifkan Cron Job Tertentu |
| `/i/<slug>/cron/<jid>/delete`| POST | Owner/Admin | Menghapus Cron Job |
| `/i/<slug>/terminal` | GET | Owner/Admin | Antarmuka Web Terminal xterm.js |
| `/i/<slug>/terminal/session` | POST | Owner/Admin | Membuka Sesi PTY Terminal Baru |
| `/i/<slug>/terminal/read/<rid>`| GET | Owner/Admin | Stream Output PTY Terminal |
| `/i/<slug>/terminal/write/<rid>`| POST | Owner/Admin | Mengirim Input Keyboard ke Shell |
| `/i/<slug>/terminal/resize/<rid>`| POST | Owner/Admin | Mengirim Perubahan Ukuran Layar Terminal |
| `/i/<slug>/terminal/stop/<rid>` | POST | Owner/Admin | Menghentikan Sesi Terminal |
| `/i/<slug>/action` | POST | Owner/Admin | Aksi Lifecycle (Start, Stop, Restart, Update) |
| `/i/<slug>/version-check` | GET | Owner/Admin | Memeriksa Hash Versi Kontainer vs Master Lokal |
| `/i/<slug>/model/quick` | POST | Owner/Admin | Mengganti Model AI Aktif (1-Klik Selector) |
| `/i/<slug>/model/chat` | POST | Owner/Admin | Mengirim Pesan Uji Coba Chat |
| `/i/<slug>/soul` | POST | Owner/Admin | Menyimpan Perubahan File Persona SOUL.md |
| `/i/<slug>/backup` | GET | Owner/Admin | Mengunduh Arsip Backup .tar.gz |
| `/i/<slug>/restore` | POST | Owner/Admin | Mengunggah dan Memulihkan File Backup |
| `/i/<slug>/qr` | GET | Owner/Admin | Mengambil Gambar QR Code WhatsApp/Telegram Real-Time |
| `/i/<slug>/wa/purge` | POST | Owner/Admin | Membersihkan Kredensial & Sesi WhatsApp Terputus |

---

## 6. SISTEM KEAMANAN & HARDENING

1. **Proteksi Zip-Slip Path Traversal**:
   - Memeriksa setiap entri arsip saat restore; menolak file dengan `..`, path absolut di luar direktori kerja, serta memblokir symlink/hardlink mencurigakan.
2. **Rate Limiting In-Memory**:
   - Membatasi request chat maksimal 30 pesan per menit per user untuk mencegah eksploitasi kuota API model.
   - Membatasi aksi lifecycle tombol minimal 1.5 detik per eksekusi.
3. **Proteksi Concurrency Database SQLite**:
   - Mengaktifkan `WAL mode` (Write-Ahead Logging) dan menyetel `PRAGMA busy_timeout = 60000` (60 detik) untuk mencegah `database is locked` saat terjadi operasi I/O tinggi bersamaan dengan polling background.
4. **Perlindungan Session & CSRF**:
   - Cookie sesi dilindungi dengan flag `HttpOnly` dan `SameSite=Lax`.
   - Form submission dilindungi dengan validasi token CSRF terenkripsi.
5. **Auto-Sanitization Log & Chat**:
   - Menyaring karakter kontrol non-printable sebelum dirender ke browser guna mencegah serangan *terminal escape injection* dan *cross-site scripting (XSS)*.

---

## 7. ANALISIS SWOT: KELEBIHAN & KEKURANGAN

### 🟢 KELEBIHAN (STRENGTHS)
1. **Penyediaan Instan (*Zero-Setup Onboarding*)**: User tidak perlu mengerti Docker atau Linux; workspace langsung aktif dalam hitungan detik.
2. **Konektivitas Chat Nyata**: Langsung terhubung ke WhatsApp dan Telegram tanpa perlu webhook server publik yang rumit.
3. **Fleksibilitas Model Luar Biasa**: Didukung oleh 9Router yang memungkinkan pergantian ratusan model AI komersial (Claude, GPT, Gemini) hingga model gratis (OpenCode, HY3).
4. **Isolasi Penuh**: Kerusakan atau proses berat di satu instance tidak merusak workspace user lain.
5. **Antarmuka Modern & Ringan**: Desain bergaya Linear/Vercel yang sangat cepat dimuat dan ramah perangkat mobile.

---

### 🟡 KEKURANGAN SAAT INI (WEAKNESSES)
1. **Single Host Dependency**: Seluruh kontainer masih berjalan di satu host server fisik yang sama (belum terdistribusi multi-node cluster/Kubernetes).
2. **Penyimpanan Log Terminal In-Memory**: Riwayat terminal xterm.js bersifat sementara per sesi koneksi browser dan belum disimpan permanen ke database audit.
3. **Ketergantungan Ekosistem WhatsApp Web (Baileys)**: Koneksi WhatsApp sewaktu-waktu dapat terputus (*unlinked*) jika nomor telepon utama mati atau WhatsApp melakukan pembaruan protokol besar.
4. **Pembayaran Masih Manual**: Aktivasi perpanjangan masa berlangganan saat ini masih harus diatur secara manual oleh admin melalui dropdown di dasbor admin.

---

## 8. PELUANG & RENCANA PENGEMBANGAN MASA DEPAN (ROADMAP)

Berikut adalah daftar peningkatan potensial yang dapat diimplementasikan pada versi-versi berikutnya:

1. **💳 Integrasi Payment Gateway Otomatis (QRIS / Tripay / Midtrans)**:
   - Pelanggan memilih paket di beranda, scan QRIS via GoPay/BCA/Dana, webhook memvalidasi pembayaran, dan sistem secara otomatis membuat akun serta instance dalam 3 detik tanpa keterlibatan admin.
2. **🌐 Subdomain Routing Otomatis (`<slug>.hermesku.my.id`)**:
   - Mengintegrasikan API Cloudflare DNS/Tunnel otomatis agar setiap instance mendapatkan subdomain unik langsung untuk Web UI 9Router atau antarmuka web kustom lainnya.
3. **🔔 Notifikasi Otomatis Bot Admin (Telegram / WA Alerts)**:
   - Bot notifikasi yang otomatis mengirim pesan ke admin jika ada server error, CPU spike di atas 90%, atau ada pengguna yang masa aktifnya akan habis.
4. **📦 Multi-Server Clustering (Docker Swarm / Nomad)**:
   - Kemampuan menyebarkan pembuatan instance ke beberapa server VPS lain jika satu server utama sudah penuh kuota RAM/CPU-nya.
5. **📊 Grafik Real-Time Resource Monitor**:
   - Menampilkan visualisasi grafik penggunaan CPU, RAM, dan I/O disk secara live di halaman dasbor pengguna menggunakan Chart.js.

---
*Dokumen ini dibuat secara otomatis dan komprehensif sebagai panduan resmi arsitektur dan operasional Hermes Workspace Manager.*

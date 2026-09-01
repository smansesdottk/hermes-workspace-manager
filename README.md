# 🚀 Hermes Workspace Manager

Platform manajemen multi-tenant dan orkestrasi container Docker untuk **Hermes Agent** dan **9Router AI Gateway**. Memberikan setiap pengguna workspace AI terdedikasi, terisolasi 100%, siap pakai, dan terintegrasi langsung dengan **WhatsApp** dan **Telegram**.

---

## ✨ Fitur Unggulan

- **🛡️ 100% Isolasi Data per User**: Setiap instance berjalan di container Docker mandiri dengan CPU/RAM limit, network bridge pribadi, dan volume penyimpanan terpisah.
- **⚡ Hermes Agent Engine**: Agen AI otonom dari Nous Research yang mampu menjalankan terminal Linux, coding, riset web, analisis file, hingga eksekusi workflow cerdas.
- **🔀 9Router AI Gateway**: Satu endpoint OpenAI-compatible terpadu untuk mengakses 690+ model LLM (Claude 3.5 Sonnet, GPT-4o, Gemini, Qwen, DeepSeek, hingga model gratis).
- **📱 Pindai QR WhatsApp & Telegram 1-Klik**: Menghubungkan asisten AI langsung ke nomor WhatsApp pribadi (Self-Chat / Mode Bot) atau Telegram Bot tanpa setup server yang rumit.
- **🖥️ Web Terminal Interaktif (xterm.js)**: Terminal PTY Bash bawaan di dalam browser untuk menguji perintah CLI, mengecek file, atau menginstal paket Python di dalam container.
- **⏰ GUI Visual Cron Jobs**: Pengguna dapat melihat, menjeda, mengaktifkan, atau membuat tugas otonom terjadwal (misal: kirim rangkuman berita tiap jam 08:00 pagi).
- **💳 Sistem Paket Masa Berlangganan (SaaS Ready)**: Pengaturan masa aktif (Trial 1-7 Hari, 1-12 Bulan, Lifetime), auto-lock container expired, notifikasi H-3, serta editor pricing di halaman depan.
- **🔄 Update 1-Klik**: Pembaruan container Hermes & 9Router secara otomatis tanpa kehilangan riwayat chat dan konfigurasi.
- **📥 Backup & Restore (.tar.gz)**: Cadangkan dan pulihkan seluruh konfigurasi, memori, dan database workspace dalam 1 kali klik.

---

## ⚡ Panduan Instalasi Cepat (1-Click Setup)

### 🐧 Linux (Ubuntu Server / Desktop / Debian)

1. **Clone repositori**:
   ```bash
   git clone https://github.com/smansesdottk/hermes-workspace-manager.git
   cd hermes-workspace-manager
   ```

2. **Jalankan Installer 1-Klik**:
   ```bash
   chmod +x install.sh run.sh
   ./install.sh
   ```

3. **Jalankan Panel**:
   ```bash
   ./run.sh
   ```
   Buka browser di: **`http://127.0.0.1:8899`**

---

### 🪟 Windows (Windows 10 / 11)

1. Pastikan **Docker Desktop** dan **Python 3.10+** sudah terpasang dan berjalan di Windows Anda.
2. Unduh / Clone repositori ini ke komputer Anda.
3. **Klik ganda file `install.bat`** untuk menyiapkan seluruh dependensi otomatis.
4. **Klik ganda file `run.bat`** untuk menjalankan panel dan otomatis membuka browser ke `http://127.0.0.1:8899`.

---

## 🛠️ Instalasi Manual (Step-by-Step)

Jika Anda ingin mengonfigurasi environment secara manual:

```bash
# 1. Buat virtual environment Python
python3 -m venv .venv
source .venv/bin/activate  # Di Windows: .venv\Scripts\activate

# 2. Pasang dependensi
pip install --upgrade pip
pip install -r requirements.txt

# 3. Inisialisasi Database
python3 -c "import manager; manager.init_db()"

# 4. Jalankan aplikasi
python3 app.py
```

---

## 👥 Akun & Peran Pengguna

- **Akun Admin Pertama Kali**:
  Saat pertama kali dijalankan, jika belum ada akun, Anda dapat mendaftarkan akun pertama melalui form registrasi atau halaman login yang otomatis menjadi Administrator.
- **Pengguna Biasa (User)**:
  User hanya memiliki akses ke workspace instance miliknya sendiri, mengatur persona `SOUL.md`, menghubungkan WhatsApp/Telegram, menguji chat model, mengelola cron job, dan download backup data.

---

## 🌐 Menghubungkan Domain Publik (Cloudflare Tunnel)

Jika ingin mempublikasikan panel ke internet dengan domain Anda sendiri:

1. Buat Cloudflare Tunnel via dashboard Cloudflare Zero Trust.
2. Arahkan Public Hostname ke:
   - **Service**: `HTTP`
   - **URL**: `127.0.0.1:8899`

---

## 📄 Lisensi
Didistribusikan di bawah lisensi MIT. Bebas digunakan, dimodifikasi, dan dikembangkan untuk kebutuhan pribadi maupun komersial.

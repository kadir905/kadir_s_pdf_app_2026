# Firebase API kurulumu

`firebase_api.py`, Flutter uygulaması ile Windows masaüstü uygulamasının ortak
not ve dosya servisini sağlar. Mevcut `pdf_studio.py` dosyasından bağımsızdır.

## 1. Firebase tarafı

Firebase Console'da:

1. Bir proje oluşturun.
2. Authentication bölümünden kullanılacak giriş yöntemini açın.
3. Firestore Database oluşturun.
4. Storage bölümünü etkinleştirin.
5. Project settings > Service accounts bölümünden Python için bir service-account
   JSON anahtarı oluşturun.

Service-account JSON dosyasını projeye veya kaynak koduna göndermeyin. Güvenli
bir klasörde saklayın.

## 2. Python bağımlılıkları

PowerShell'de proje klasöründe:

```powershell
py -m pip install -r requirements-firebase.txt
```

## 3. Ortam değişkenleri

Geçerli PowerShell oturumu için örnek:

```powershell
$env:FIREBASE_SERVICE_ACCOUNT = 'C:\guvenli-klasor\firebase-service-account.json'
$env:FIREBASE_STORAGE_BUCKET = 'proje-id.firebasestorage.app'
```

## 4. API'yi çalıştırma

```powershell
py -m uvicorn firebase_api:app --reload --port 8000
```

Flutter uygulaması geliştirme sırasında API adresi Android emülatöründe
`http://10.0.2.2:8000`, gerçek telefonda ise bilgisayarın yerel ağ IP adresi
olmalıdır. Gerçek telefondan erişim için telefon ve bilgisayar aynı Wi-Fi
ağında olmalı ve Windows Güvenlik Duvarı'nda ilgili porta izin verilmelidir.

## API özeti

- `GET /health` — servis kontrolü
- `GET /notes` — kullanıcının notlarını getirir
- `POST /notes` — not oluşturur
- `PATCH /notes/{id}` — notu günceller
- `DELETE /notes/{id}` — notu siler
- `POST /files` — PDF veya desteklenen dosyayı yükler
- `GET /files/{storage_path}` — kullanıcının dosyasını indirir

`/notes` ve `/files` uç noktaları, Flutter Firebase Authentication tarafından
alınan ID token'ı şu başlıkla bekler:

```text
Authorization: Bearer FIREBASE_ID_TOKEN
```

Üretime geçmeden önce `firebase_api.py` içindeki CORS ayarı `allow_origins=["*"]`
yerine yalnızca izin verilen istemci adresleriyle sınırlandırılmalıdır.

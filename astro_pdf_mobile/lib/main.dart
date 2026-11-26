import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:speech_to_text/speech_to_text.dart';

import 'firebase_options.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
  await GoogleSignIn.instance.initialize(
    serverClientId: '691480479686-0v655lulvcskb59rg7dvv0ar0ltm2a9u.apps.googleusercontent.com',
  );
  runApp(const AstroPdfApp());
}

class AstroPdfApp extends StatefulWidget {
  const AstroPdfApp({super.key});

  @override
  State<AstroPdfApp> createState() => _AstroPdfAppState();
}

class _AstroPdfAppState extends State<AstroPdfApp> {
  ThemeMode themeMode = ThemeMode.light;

  void toggleTheme() {
    setState(() {
      themeMode = themeMode == ThemeMode.light
          ? ThemeMode.dark
          : ThemeMode.light;
    });
  }

  ThemeData _theme(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xff4f46e5),
      brightness: brightness,
    );
    return ThemeData(
      colorScheme: scheme,
      brightness: brightness,
      useMaterial3: true,
      scaffoldBackgroundColor: isDark
          ? const Color(0xff101116)
          : const Color(0xfff7f8fc),
      appBarTheme: AppBarTheme(
        backgroundColor: isDark
            ? const Color(0xff101116)
            : const Color(0xfff7f8fc),
        surfaceTintColor: Colors.transparent,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: isDark ? const Color(0xff1b1d25) : Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: isDark ? const Color(0xff1b1d25) : Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: scheme.primary, width: 1.5),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = themeMode == ThemeMode.dark;
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AstroPDF Studio',
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: themeMode,
      home: StreamBuilder<User?>(
        stream: FirebaseAuth.instance.authStateChanges(),
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Scaffold(
              body: Center(child: CircularProgressIndicator()),
            );
          }
          if (snapshot.data == null) {
            return LoginPage(isDark: isDark, onToggleTheme: toggleTheme);
          }
          return NotesPage(isDark: isDark, onToggleTheme: toggleTheme);
        },
      ),
    );
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({
    required this.isDark,
    required this.onToggleTheme,
    super.key,
  });

  final bool isDark;
  final VoidCallback onToggleTheme;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final email = TextEditingController();
  final password = TextEditingController();
  bool register = false;
  bool busy = false;
  String? error;

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    super.dispose();
  }

  Future<void> submit() async {
    if (email.text.trim().isEmpty || password.text.length < 6) {
      setState(
        () => error = 'E-posta girin ve en az 6 karakterli bir şifre kullanın.',
      );
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final auth = FirebaseAuth.instance;
      if (register) {
        await auth.createUserWithEmailAndPassword(
          email: email.text.trim(),
          password: password.text,
        );
      } else {
        await auth.signInWithEmailAndPassword(
          email: email.text.trim(),
          password: password.text,
        );
      }
    } on FirebaseAuthException catch (exception) {
      setState(() => error = _authMessage(exception.code));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> signInWithGoogle() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final googleUser = await GoogleSignIn.instance.authenticate();
      final googleAuth = googleUser.authentication;
      final credential = GoogleAuthProvider.credential(
        idToken: googleAuth.idToken,
      );
      await FirebaseAuth.instance.signInWithCredential(credential);
    } on FirebaseAuthException catch (exception) {
      setState(() => error = _authMessage(exception.code));
    } on GoogleSignInException catch (exception) {
      setState(() => error = 'Google girişi başarısız oldu: ${exception.code}');
    } catch (exception) {
      setState(() => error = 'Google girişi başarısız oldu: $exception');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  String _authMessage(String code) {
    switch (code) {
      case 'email-already-in-use':
        return 'Bu e-posta zaten kayıtlı.';
      case 'invalid-credential':
        return 'E-posta veya şifre hatalı.';
      case 'weak-password':
        return 'Şifre en az 6 karakter olmalı.';
      case 'invalid-email':
        return 'Geçerli bir e-posta girin.';
      default:
        return 'Giriş işlemi başarısız oldu: $code';
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        actions: [
          IconButton(
            tooltip: widget.isDark ? 'Açık temaya geç' : 'Koyu temaya geç',
            onPressed: widget.onToggleTheme,
            icon: Icon(widget.isDark ? Icons.light_mode : Icons.dark_mode),
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 10, 24, 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Container(
                    width: 112,
                    height: 112,
                    decoration: BoxDecoration(
                      color: scheme.primaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: ClipOval(
                      child: Image.asset(
                        'assets/astropdf_logo.png',
                        fit: BoxFit.cover,
                      ),
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    'AstroPDF Studio',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineMedium
                        ?.copyWith(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    register
                        ? 'Kişisel çalışma alanınızı oluşturun'
                        : 'Notlarınıza her yerden ulaşın',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodyLarge,
                  ),
                  const SizedBox(height: 28),
                  TextField(
                    controller: email,
                    keyboardType: TextInputType.emailAddress,
                    decoration: const InputDecoration(
                      labelText: 'E-posta adresi',
                      prefixIcon: Icon(Icons.email_outlined),
                    ),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: password,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'Şifre',
                      prefixIcon: Icon(Icons.lock_outline),
                    ),
                  ),
                  if (error != null) ...[
                    const SizedBox(height: 14),
                    Text(error!, style: TextStyle(color: scheme.error)),
                  ],
                  const SizedBox(height: 20),
                  FilledButton.icon(
                    onPressed: busy ? null : submit,
                    icon: busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.login),
                    label: Text(register ? 'Hesap oluştur' : 'Giriş yap'),
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: busy || register ? null : signInWithGoogle,
                    icon: const Icon(Icons.account_circle_outlined),
                    label: const Text('Google ile devam et'),
                  ),
                  TextButton(
                    onPressed: busy
                        ? null
                        : () => setState(() {
                            register = !register;
                            error = null;
                          }),
                    child: Text(
                      register ? 'Zaten hesabım var' : 'Yeni hesap oluştur',
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class NotesPage extends StatefulWidget {
  const NotesPage({
    required this.isDark,
    required this.onToggleTheme,
    super.key,
  });

  final bool isDark;
  final VoidCallback onToggleTheme;

  @override
  State<NotesPage> createState() => _NotesPageState();
}

class _NotesPageState extends State<NotesPage> {
  final search = TextEditingController();
  final textToSpeech = FlutterTts();
  String query = '';
  String sourceFilter = 'all';

  CollectionReference<Map<String, dynamic>> get notes =>
      FirebaseFirestore.instance.collection('notes');

  @override
  void dispose() {
    search.dispose();
    textToSpeech.stop();
    super.dispose();
  }

  Future<void> _readAloud(String text) async {
    if (text.trim().isEmpty) return;
    await textToSpeech.setLanguage('tr-TR');
    await textToSpeech.setSpeechRate(0.48);
    await textToSpeech.speak(text);
  }

  String _text(Map<String, dynamic> data, String first, String second) =>
      data[first] as String? ?? data[second] as String? ?? '';

  Future<void> editNote(
    BuildContext context, {
    DocumentSnapshot<Map<String, dynamic>>? document,
  }) async {
    final data = document?.data() ?? <String, dynamic>{};
    final title = TextEditingController(text: _text(data, 'baslik', 'title'));
    final content = TextEditingController(
      text: _text(data, 'icerik', 'content'),
    );
    final speech = SpeechToText();
    var listening = false;
    StateSetter? dialogState;

    Future<void> listenAgain() async {
      if (!listening || !context.mounted) return;
      await speech.listen(
        listenOptions: SpeechListenOptions(localeId: 'tr_TR'),
        onResult: (result) {
          content.text = result.recognizedWords;
          content.selection = TextSelection.fromPosition(
            TextPosition(offset: content.text.length),
          );
          dialogState?.call(() {});
        },
      );
    }

    final result = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) {
          dialogState = setDialogState;
          return AlertDialog(
            insetPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 24,
            ),
            title: Row(
              children: [
                Expanded(
                  child: Text(document == null ? 'Yeni not' : 'Notu düzenle'),
                ),
                IconButton(
                  tooltip: 'Metni seslendir',
                  icon: const Icon(Icons.volume_up_outlined),
                  onPressed: () => _readAloud(content.text),
                ),
              ],
            ),
            content: SizedBox(
              width: double.maxFinite,
              height: MediaQuery.sizeOf(context).height * 0.62,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    controller: title,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      labelText: 'Başlık',
                      prefixIcon: Icon(Icons.title),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Expanded(
                    child: TextField(
                      controller: content,
                      maxLines: null,
                      expands: true,
                      textAlignVertical: TextAlignVertical.top,
                      textCapitalization: TextCapitalization.sentences,
                      decoration: InputDecoration(
                        labelText: 'İçerik',
                        alignLabelWithHint: true,
                        prefixIcon: const Padding(
                          padding: EdgeInsets.only(bottom: 190),
                          child: Icon(Icons.notes_outlined),
                        ),
                        suffixIcon: IconButton(
                          tooltip: listening
                              ? 'Dinlemeyi durdur'
                              : 'Konuşarak yaz',
                          icon: Icon(
                            listening
                                ? Icons.stop_circle_outlined
                                : Icons.mic_none,
                          ),
                          onPressed: () async {
                            if (listening) {
                              setDialogState(() => listening = false);
                              await speech.stop();
                              return;
                            }
                            final available = await speech.initialize(
                              onStatus: (status) {
                                if (status == 'done' ||
                                    status == 'notListening') {
                                  if (listening) {
                                    Future<void>.delayed(
                                      const Duration(milliseconds: 250),
                                      listenAgain,
                                    );
                                  }
                                }
                              },
                            );
                            if (!available) return;
                            setDialogState(() => listening = true);
                            await listenAgain();
                          },
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    listening
                        ? 'Dinliyorum... Konuşmayı bitirince durdurabilirsin.'
                        : 'Mikrofonla konuşarak notunu yazdırabilirsin.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () async {
                  await speech.stop();
                  if (context.mounted) Navigator.pop(context, false);
                },
                child: const Text('İptal'),
              ),
              FilledButton.icon(
                onPressed: () async {
                  await speech.stop();
                  if (context.mounted) Navigator.pop(context, true);
                },
                icon: const Icon(Icons.save_outlined),
                label: const Text('Kaydet'),
              ),
            ],
          );
        },
      ),
    );
    if (result != true || title.text.trim().isEmpty) return;
    final now = Timestamp.now();
    final payload = {
      'baslik': title.text.trim(),
      'icerik': content.text.trim(),
      'ownerUid': FirebaseAuth.instance.currentUser!.uid,
      if (document == null) 'source': 'android',
      'updatedAt': now,
      if (document == null) 'createdAt': now,
    };
    if (document == null) {
      await notes.add(payload);
    } else {
      await document.reference.update(payload);
    }
  }

  Future<void> deleteNote(
    BuildContext context,
    DocumentSnapshot<Map<String, dynamic>> document,
  ) async {
    final answer = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Not silinsin mi?'),
        content: const Text('Bu işlem geri alınamaz.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('İptal'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Sil'),
          ),
        ],
      ),
    );
    if (answer == true) await document.reference.delete();
  }

  Widget _statCard(
    BuildContext context,
    IconData icon,
    String label,
    String value,
  ) {
    final scheme = Theme.of(context).colorScheme;
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: scheme.primary),
              const SizedBox(height: 12),
              Text(
                value,
                style: Theme.of(context).textTheme.headlineSmall
                    ?.copyWith(fontWeight: FontWeight.w800),
              ),
              Text(label, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
      ),
    );
  }

  Widget _noteCard(
    BuildContext context,
    DocumentSnapshot<Map<String, dynamic>> document,
  ) {
    final data = document.data() ?? <String, dynamic>{};
    final rawTitle = _text(data, 'baslik', 'title');
    final title = rawTitle.isEmpty ? 'Başlıksız not' : rawTitle;
    final content = _text(data, 'icerik', 'content');
    final isDesktopNote = data['source'] == 'python_desktop';
    final scheme = Theme.of(context).colorScheme;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(22),
        onTap: () => editNote(context, document: document),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 18, 10, 18),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: scheme.primaryContainer,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Icon(Icons.description_outlined, color: scheme.primary),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        fontWeight: FontWeight.w700,
                        fontSize: 16,
                      ),
                    ),
                    if (isDesktopNote) ...[
                      const SizedBox(height: 6),
                      Text(
                        'Bilgisayardan senkronize',
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: scheme.primary,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                    const SizedBox(height: 7),
                    Text(
                      content.isEmpty ? 'İçerik eklenmemiş.' : content,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium
                          ?.copyWith(height: 1.35),
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Notu sesli oku',
                icon: const Icon(Icons.volume_up_outlined),
                onPressed: () => _readAloud('$title. $content'),
              ),
              IconButton(
                tooltip: 'Notu sil',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => deleteNote(context, document),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = FirebaseAuth.instance.currentUser;
    final name = user?.displayName?.split(' ').first ?? 'Çalışma arkadaşım';
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'AstroPDF',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        actions: [
          PopupMenuButton<String>(
            tooltip: 'Not kaynağı',
            initialValue: sourceFilter,
            onSelected: (value) => setState(() => sourceFilter = value),
            itemBuilder: (context) => const [
              PopupMenuItem(value: 'all', child: Text('Tüm notlar')),
              PopupMenuItem(
                value: 'desktop',
                child: Text('Bilgisayardan gelenler'),
              ),
              PopupMenuItem(
                value: 'android',
                child: Text('Telefondaki notlar'),
              ),
            ],
            icon: const Icon(Icons.menu_rounded),
          ),
          IconButton(
            tooltip: widget.isDark ? 'Açık temaya geç' : 'Koyu temaya geç',
            onPressed: widget.onToggleTheme,
            icon: Icon(widget.isDark ? Icons.light_mode : Icons.dark_mode),
          ),
          IconButton(
            tooltip: 'Çıkış yap',
            onPressed: () => FirebaseAuth.instance.signOut(),
            icon: const Icon(Icons.logout),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: StreamBuilder<QuerySnapshot<Map<String, dynamic>>>(
        stream: notes.snapshots(),
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Center(
              child: Text(
                'Notlar yüklenemedi.\n${snapshot.error}',
                textAlign: TextAlign.center,
              ),
            );
          }
          if (!snapshot.hasData)
            return const Center(child: CircularProgressIndicator());
          final allDocuments = snapshot.data!.docs;
          final documents = allDocuments.where((document) {
            final data = document.data();
            final isDesktopNote = data['source'] == 'python_desktop';
            final matchesSource =
                sourceFilter == 'all' ||
                (sourceFilter == 'desktop' && isDesktopNote) ||
                (sourceFilter == 'android' && !isDesktopNote);
            if (!matchesSource) return false;
            if (query.trim().isEmpty) return true;
            return '${_text(data, 'baslik', 'title')} ${_text(data, 'icerik', 'content')}'
                .toLowerCase()
                .contains(query.toLowerCase());
          }).toList();

          return RefreshIndicator(
            onRefresh: () async => setState(() {}),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(18, 8, 18, 110),
              children: [
                Text(
                  'Merhaba, $name 👋',
                  style: Theme.of(context).textTheme.headlineSmall
                      ?.copyWith(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 5),
                Text(
                  'Bugün ne çalışmak istersin?',
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
                const SizedBox(height: 20),
                Row(
                  children: [
                    _statCard(
                      context,
                      Icons.note_alt_outlined,
                      'Toplam not',
                      '${allDocuments.length}',
                    ),
                    const SizedBox(width: 12),
                    _statCard(
                      context,
                      Icons.search_rounded,
                      'Bulunan not',
                      '${documents.length}',
                    ),
                  ],
                ),
                const SizedBox(height: 22),
                TextField(
                  controller: search,
                  onChanged: (value) => setState(() => query = value),
                  decoration: InputDecoration(
                    hintText: 'Notlarında ara...',
                    prefixIcon: const Icon(Icons.search),
                    suffixIcon: query.isEmpty
                        ? null
                        : IconButton(
                            icon: const Icon(Icons.clear),
                            onPressed: () {
                              search.clear();
                              setState(() => query = '');
                            },
                          ),
                  ),
                ),
                const SizedBox(height: 22),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      sourceFilter == 'desktop'
                          ? 'Bilgisayar notları'
                          : sourceFilter == 'android'
                          ? 'Telefon notları'
                          : 'Notlarım',
                      style: Theme.of(context).textTheme.titleLarge
                          ?.copyWith(fontWeight: FontWeight.w800),
                    ),
                    Text(
                      '${documents.length} kayıt',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                if (documents.isEmpty)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(30),
                      child: Column(
                        children: [
                          Icon(
                            Icons.lightbulb_outline,
                            size: 46,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                          const SizedBox(height: 12),
                          Text(
                            query.isEmpty
                                ? 'Henüz not yok'
                                : 'Aramanla eşleşen not yok',
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            query.isEmpty
                                ? 'İlk notunu ekleyerek çalışmaya başla.'
                                : 'Farklı bir kelime deneyebilirsin.',
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    ),
                  )
                else
                  ...documents.map(
                    (document) => Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: _noteCard(context, document),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => editNote(context),
        icon: const Icon(Icons.add_rounded),
        label: const Text('Yeni not'),
      ),
    );
  }
}

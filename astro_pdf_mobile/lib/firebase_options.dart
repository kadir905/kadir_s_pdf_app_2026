import 'package:firebase_core/firebase_core.dart';

/// Firebase settings for com.kadiroral.astro_pdf_mobile.
class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform => android;

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyBGwRSH1gBwjoavR7Wqyx1sOunOVMWhOAw',
    appId: '1:691480479686:android:a1ba64603a37db583894c7',
    messagingSenderId: '691480479686',
    projectId: 'derscalismauygulamam',
  );
}

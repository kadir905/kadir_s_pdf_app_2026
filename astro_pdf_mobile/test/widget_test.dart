import 'package:flutter_test/flutter_test.dart';

import 'package:astro_pdf_mobile/firebase_options.dart';

void main() {
  test('Firebase project configuration is present', () {
    expect(DefaultFirebaseOptions.android.projectId, 'derscalismauygulamam');
    expect(DefaultFirebaseOptions.android.appId, contains('android:'));
  });
}

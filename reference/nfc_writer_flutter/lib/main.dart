// ═══════════════════════════════════════════════════════════════════════════════
// [앱용] NFC 영상 ID 기록기 (NFC Writer)
// ═══════════════════════════════════════════════════════════════════════════════
// 이 코드는 슬롯 배송 전, 앱을 켠 휴대폰을 NFC 슬롯에 갖다 대서
// "이 판은 크리스마스 영상용이야" 같은 영상 고유 ID를 NFC 태그에 기록합니다.
// ═══════════════════════════════════════════════════════════════════════════════

import 'package:flutter/material.dart';
import 'package:nfc_manager/nfc_manager.dart';
import 'package:nfc_manager/platform_tags.dart';

void main() {
  runApp(const NFCWriterApp());
}

class NFCWriterApp extends StatelessWidget {
  const NFCWriterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '이터널빔 NFC 기록기',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.deepPurple),
        useMaterial3: true,
      ),
      home: const NFCWriterScreen(),
    );
  }
}

class NFCWriterScreen extends StatefulWidget {
  const NFCWriterScreen({super.key});

  @override
  State<NFCWriterScreen> createState() => _NFCWriterScreenState();
}

class _NFCWriterScreenState extends State<NFCWriterScreen> {
  // 기록할 영상 ID 목록 (예: 크리스마스, 석양해변 등)
  String _selectedVideoId = 'christmas_wonder';

  final Map<String, String> _videoOptions = {
    'christmas_wonder': '크리스마스 원더',
    'sunset_beach': '석양 해변',
    'forest_dream': '숲의 꿈',
    'memorial_light': '추모의 빛',
    'galaxy_trip': '우주 여행',
    'sakura_garden': '벚꽃 정원',
    'ocean_dive': '심해 탐험',
    'aurora_light': '오로라 빛',
  };

  String _statusMessage = 'NFC 태그를 휴대폰에 갖다 대세요';
  bool _isWriting = false;

  /// NFC 태그에 영상 ID를 기록하는 함수
  /// - NdefFormatable: 비어 있는 태그에 처음 기록할 때 사용
  /// - Ndef: 이미 NDEF가 설정된 태그에 덮어쓸 때 사용
  Future<void> _writeToNfc(String videoId) async {
    if (_isWriting) return;

    setState(() {
      _isWriting = true;
      _statusMessage = 'NFC 태그를 기다리는 중...';
    });

    try {
      await NfcManager.instance.startSession(
        onDiscovered: (NfcTag tag) async {
          // NFC 태그를 발견했을 때 실행됨

          // Ndef 태그인지 확인 (NDEF = NFC Data Exchange Format, NFC 표준 데이터 형식)
          final ndef = Ndef.from(tag);

          if (ndef == null) {
            setState(() {
              _statusMessage = '이 태그는 기록할 수 없습니다.';
              _isWriting = false;
            });
            await NfcManager.instance.stopSession();
            return;
          }

          try {
            if (ndef.isWritable) {
              // 기록할 메시지 생성
              // 텍스트 레코드: "EB_VIDEO:christmas_wonder" 형식
              // EB_VIDEO = Eternal Beam Video 접두사 (앱/프로젝터가 이 형식만 인식)
              final String recordText = 'EB_VIDEO:$videoId';

              final message = NdefMessage([
                NdefRecord.createText(recordText),
              ]);

              // 태그에 기록
              await ndef.write(message);

              setState(() {
                _statusMessage = '기록 완료! $_videoOptions[videoId]';
                _isWriting = false;
              });
            } else {
              setState(() {
                _statusMessage = '이 태그는 이미 잠겨 있어 기록할 수 없습니다.';
                _isWriting = false;
              });
            }
          } catch (e) {
            setState(() {
              _statusMessage = '기록 실패: $e';
              _isWriting = false;
            });
          }

          await NfcManager.instance.stopSession();
        },
      );
    } catch (e) {
      setState(() {
        _statusMessage = 'NFC를 사용할 수 없습니다: $e';
        _isWriting = false;
      });
    }
  }

  @override
  void dispose() {
    NfcManager.instance.stopSession();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('이터널빔 NFC 기록기'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 32),

            // 1. 영상 선택
            const Text(
              '어떤 영상으로 기록할까요?',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              value: _selectedVideoId,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                labelText: '영상 테마',
              ),
              items: _videoOptions.entries
                  .map((e) => DropdownMenuItem(
                        value: e.key,
                        child: Text(e.value),
                      ))
                  .toList(),
              onChanged: _isWriting
                  ? null
                  : (value) {
                      if (value != null) {
                        setState(() => _selectedVideoId = value);
                      }
                    },
            ),
            const SizedBox(height: 32),

            // 2. 기록 버튼
            FilledButton.icon(
              onPressed: _isWriting
                  ? null
                  : () => _writeToNfc(_selectedVideoId),
              icon: _isWriting
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.nfc),
              label: Text(_isWriting ? '태그 대기 중...' : 'NFC 기록 시작'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 16),
                textStyle: const TextStyle(fontSize: 18),
              ),
            ),
            const SizedBox(height: 32),

            // 3. 상태 메시지
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.grey.shade200,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                _statusMessage,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 16),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

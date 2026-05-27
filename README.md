# Semi-auto

반도체 측정 장비를 Python(PyVISA)으로 제어하기 위한 코드 모음입니다.

- **Keysight B1500A** — 반도체 파라미터 분석기 (I-V 측정 등)
- **Cascade S300** — 반자동 프로버 (서브사이트 그리드 제어)
- **VNA** — 벡터 네트워크 분석기 (SCPI 제어)

## 환경 설정

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 구성

```
semi-auto/
├── examples/        # 장비별 예제 & 기능 검증 코드
│   ├── b1500/       #   - B1500 I-V 측정 노트북
│   ├── s300/        #   - S300 프로버 제어 노트북
│   └── vna/         #   - VNA SCPI 제어 예제 (.py / .xlsm)
├── projects/        # 실제 측정/자동화 프로젝트 (projects/<프로젝트명>/)
│   └── old/         #   - 이관 이전의 기존 측정 노트북
├── utils/           # 공용 유틸 (서브사이트 그리드 CSV 생성/수정)
└── docs/
    └── manuals/     # 장비 프로그래밍 가이드 (PDF)
```

| 구분 | 위치 |
|---|---|
| 장비별 예제 | `examples/<장비명>/` |
| 실제 프로젝트 | `projects/<프로젝트명>/` |
| 공용 유틸리티 | `utils/` |
| 매뉴얼·문서 | `docs/`, `docs/manuals/` |

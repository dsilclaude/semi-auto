# Grid Measure — 동작 흐름도 (Flow Chart)

S300 프로버(좌표 이동) + B1500A(I-V 측정) 통합 측정 흐름.
노트북 [grid_measure.ipynb](grid_measure.ipynb) 의 셀 순서와 1:1 대응.

```mermaid
flowchart TD
    subgraph MAN["① 사람이 수동으로 (Nucleus UI)"]
        A1["척 로드 / 진공 ON / 소자 세팅"]
        A2["Alignment (2-point)"]
        A3["Tipping / Set Contact (contact 높이 잡기)"]
        A4["첫 소자에 팁 직접 contact"]
        A1 --> A2 --> A3 --> A4
    end

    subgraph CODE["② 코드 (노트북)"]
        B["장비 연결<br/>S300 REMOTE + metric, B1500 init"]
        C{"통신 확인<br/>check_comm()"}
        C1["주소 / REMOTE / 정렬 점검"]
        D["좌표 파일 읽기<br/>CSV / 엑셀 자동 판별"]
        E["기준점 등록 set_reference()<br/>현재 XY → 원점(0,0)<br/>현재 Z → contact 높이"]

        F(("좌표 루프<br/>각 행마다"))
        G{"좌표 == (0,0)?<br/>= 사람이 contact한 원점"}
        H["separate() 분리"]
        I["move_xy(dx,dy)<br/>Z 자동분리 후 XY 이동"]
        K["contact() 접촉<br/>(set된 높이로, 그 아래로 안 내려감)"]
        J["B1500 I-V 측정<br/>measure_iv (SMU_CONFIG 따라 G/D/S/B)"]
        L["CSV 저장<br/>results/subsite_N.csv"]
        M{"다음 좌표 있음?"}
        N["마무리<br/>separate() + 원점 복귀"]
        O(["완료"])
    end

    A4 --> B
    B --> C
    C -- "FAIL" --> C1 --> C
    C -- "OK" --> D
    D --> E
    E --> F
    F --> G
    G -- "예 (원점)" --> J
    G -- "아니오" --> H --> I --> K --> J
    J --> L --> M
    M -- "있음" --> F
    M -- "없음" --> N --> O
```

## 핵심 포인트

| 단계 | 무엇을 / 왜 |
|---|---|
| **수동 준비** | align·tipping·set contact·첫 소자 contact 는 코드가 대체 안 함 (전제) |
| **통신 확인** | S300·B1500 둘 다 응답 + S300 REMOTE/정렬 상태 확인. OK 떠야 진행 |
| **기준점 등록** | 사람이 contact시킨 위치를 원점(0,0)·contact 높이로 등록 → 이후 소자 안 찍힘 |
| **(0,0) 분기** | 좌표 (0,0) = 원점 = 이미 contact → 이동 없이 측정 (그 외만 이동) |
| **이동 안전성** | `move_xy`는 Z를 안전높이로 자동 분리 후 XY 이동 (이동 중 안 긁힘) / `contact`는 set 높이 아래로 안 내려감 |
| **팁 역할** | `SMU_CONFIG`로 팁(SMU)별 Gate/Drain/Source/Bulk = sweep/const/off 선택 |

---

### (참고) 텍스트 버전

```
[수동 준비: 척/진공/align/tipping/첫 소자 contact]
        │
        ▼
[장비 연결] ──▶ [통신 확인]──FAIL──▶[점검]──┐
                    │ OK                    │
                    ▼                       └──(재확인)
              [좌표 파일 읽기 (CSV/엑셀)]
                    │
                    ▼
          [기준점 등록: 현재위치=원점(0,0)+contact높이]
                    │
                    ▼
        ┌───────[ 좌표 루프: 각 행 ]◀──────────┐
        │            │                         │
        │      좌표 ==(0,0)? ──예──┐           │
        │            │ 아니오       │           │
        │            ▼             │           │
        │     [separate 분리]      │           │
        │            ▼             │           │
        │  [move_xy 이동(Z자동분리)]│           │
        │            ▼             │           │
        │     [contact 접촉]       │           │
        │            ▼             ▼           │
        │        [B1500 I-V 측정]              │
        │            ▼                         │
        │      [CSV 저장 subsite_N.csv]        │
        │            ▼                         │
        │       다음 좌표? ──있음──────────────┘
        │            │ 없음
        ▼            ▼
   [마무리: separate + 원점 복귀] ──▶ [완료]
```

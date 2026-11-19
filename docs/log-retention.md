# 픽메뉴 로그 보존

Ubuntu의 `pickmenu-demo.service` 로그는 `pickmenu` 저널로 분리한다. 오래된 저널 파일은 14일을 기준으로 정리하고, 64MiB에 가까워지면 오래된 보관 파일부터 정리한다. 활성 저널의 교체 주기는 최대 하루, 개별 파일 설정은 8MiB다. 하루 한 번 실행되는 타이머가 활성 파일을 교체하고 보관 파일을 정리한다. 보존 기간은 저널 파일의 시간 범위에 따라 적용된다.

Ubuntu 저장소 루트에서 한 번 실행한다.

```sh
sudo install -m 0644 deploy/systemd/journald@pickmenu.conf /etc/systemd/journald@pickmenu.conf
sudo install -D -m 0644 deploy/systemd/pickmenu-journal.conf /etc/systemd/system/pickmenu-demo.service.d/journal.conf
sudo install -m 0644 deploy/systemd/pickmenu-journal-retention.service deploy/systemd/pickmenu-journal-retention.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart systemd-journald@pickmenu.service pickmenu-demo.service
sudo systemctl enable --now pickmenu-journal-retention.timer
```

Ubuntu에서 로그와 다음 정리 일정을 확인한다.

```sh
sudo journalctl --namespace=pickmenu -u pickmenu-demo -n 50 --no-pager
sudo systemctl list-timers pickmenu-journal-retention.timer
```

이전 픽메뉴 로그는 기본 시스템 저널에 있으므로 기존 VM 정책을 따른다. PostgreSQL 파일 로그는 Ubuntu의 `logrotate.timer`가 관리한다. 기본 저널을 전체 정리하면 SSH와 운영체제 로그까지 영향을 받는다.

Windows의 픽메뉴 GPU 실행기는 `ollama.log`와 `llm-tunnel.log`를 각각 약 8MiB에서 교체한다. 지난 로그는 `.1`부터 `.4`까지 보관하고, 교체할 때 가장 오래된 파일을 정리한다. 기존 실행기가 직접 로그를 잡고 있었다면 해당 GPU 서버와 터널을 새 실행기로 다시 시작해야 처리 방식이 바뀐다. 이전에 크게 쌓인 로그는 첫 교체 때 넘겨받아 이후 교체 순서에 따라 정리된다.

웹 접속 로그에는 표준화한 경로, 메서드, 상태 코드만 기록한다. DB 오류 진단에는 오류 종류를 남긴다. 요청 본문, 주소 검색어, 임의 경로 식별자, DB 오류 문자열을 운영 로그에 넣지 않는다.

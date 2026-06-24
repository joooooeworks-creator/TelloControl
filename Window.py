import cv2
import pygame
from djitellopy import Tello
import numpy as np


tello = Tello()
tello.connect()
tello.streamon()

pygame.init()
width, height = 960, 720
screen = pygame.display.set_mode((width, height))
pygame.display.set_caption("Tello Control with WASD")

# フォント設定（HUD風）
font = pygame.font.SysFont("monospace", 24, bold=True)
small_font = pygame.font.SysFont("monospace", 18)

clock = pygame.time.Clock()

running = True
speed = 40  #移動速度
is_flying = False  #飛行状態

# control send timing
SEND_INTERVAL_MS = 50
last_send = pygame.time.get_ticks()

# 加速用変数
base_speed = 40
max_speed = 100
accel_rate = 20  # 速度増加率 (units per second)
key_press_times = {}  # 各キーの押下開始時間

while running:
    # イベント処理（テイクオフ・ランドやフリップなどはここで即時対応）
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                try:
                    tello.emergency()
                except Exception as e:
                    print(f"Emergency failed: {e}")
                is_flying = False
                print("EMERGENCY STOP")
                
            if event.key == pygame.K_SPACE:
                if not is_flying:
                    try:
                        tello.takeoff()
                        is_flying = True
                        print("Takeoff")
                    except Exception as e:
                        print(f"Takeoff failed: {e}")
                else:
                    try:
                        tello.land()
                        is_flying = False
                        print("Land")
                    except Exception as e:
                        print(f"Land failed: {e}")
            # 加速用: キー押下開始時間を記録
            if event.key in [pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d, pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT]:
                key_press_times[event.key] = pygame.time.get_ticks()
                
        if event.type == pygame.KEYUP:

            if event.key in key_press_times:
                del key_press_times[event.key]

            if is_flying:

                now = pygame.time.get_ticks()

                def get_speed_for_key(key):
                    if key in key_press_times:
                        time_pressed = (
                            now - key_press_times[key]
                        ) / 1000.0

                        return min(
                            max_speed,
                            base_speed + time_pressed * accel_rate
                        )

                    return 0

                fb = (
                    get_speed_for_key(pygame.K_w)
                    - get_speed_for_key(pygame.K_s)
                )

                lr = (
                    get_speed_for_key(pygame.K_RIGHT)
                    - get_speed_for_key(pygame.K_LEFT)
                )

                ud = (
                    get_speed_for_key(pygame.K_UP)
                    - get_speed_for_key(pygame.K_DOWN)
                )

                yaw = (
                    get_speed_for_key(pygame.K_d)
                    - get_speed_for_key(pygame.K_a)
                )

                tello.send_rc_control(
                    int(lr),
                    int(fb),
                    int(ud),
                    int(yaw)
                )

    # キー押しっぱなし状態
    keys = pygame.key.get_pressed()

    # 送信ループ: 50msごとにコントロール値を送る
    now = pygame.time.get_ticks()
    if now - last_send >= SEND_INTERVAL_MS:
        last_send = now
        lr = 0
        fb = 0
        ud = 0
        yaw = 0

        if is_flying:  #飛んでいるときだけ操作可能
            # 各キーの速度を計算（加速機能）
            def get_speed_for_key(key):
                if key in key_press_times:
                    time_pressed = (now - key_press_times[key]) / 1000.0  # 秒
                    return min(max_speed, base_speed + time_pressed * accel_rate)
                return 0

            fb = get_speed_for_key(pygame.K_w) - get_speed_for_key(pygame.K_s)
            lr = get_speed_for_key(pygame.K_RIGHT) - get_speed_for_key(pygame.K_LEFT)
            ud = get_speed_for_key(pygame.K_UP) - get_speed_for_key(pygame.K_DOWN)
            yaw = get_speed_for_key(pygame.K_d) - get_speed_for_key(pygame.K_a)

            # デバッグ: キー押下状態を表示
            if any([keys[k] for k in [pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d, pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT]]):
                print(f"Keys: W={keys[pygame.K_w]} S={keys[pygame.K_s]} A={keys[pygame.K_a]} D={keys[pygame.K_d]} UP={keys[pygame.K_UP]} DOWN={keys[pygame.K_DOWN]} LEFT={keys[pygame.K_LEFT]} RIGHT={keys[pygame.K_RIGHT]} -> lr={lr:.1f} fb={fb:.1f} ud={ud:.1f} yaw={yaw:.1f}")

        tello.send_rc_control(int(lr), int(fb), int(ud), int(yaw))

    #カメラフレーム取得
    frame = tello.get_frame_read().frame

    if frame is None:
        continue


    # カメラ画像を左右反転
    frame = cv2.flip(frame, 1)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    # 青チャネルを減衰（50%に）
    # RGBなので index 2 が青
    frame[:, :, 2] //= 2
    frame = np.rot90(frame)
    frame_surface = pygame.surfarray.make_surface(frame)

    # センサー情報取得（HUD用）
    try:
        battery = tello.get_battery()
    except Exception as e:
        print(f"Battery error: {e}")
        battery = 0

    try:
        height = tello.get_height()
    except Exception as e:
        print(f"Height error: {e}")
        height = 0

    try:
        state = tello.get_current_state()

        speed_x = state.get("vgx", 0)
        speed_y = state.get("vgy", 0)
        speed_z = state.get("vgz", 0)

        speed = (
            speed_x**2 +
            speed_y**2 +
            speed_z**2
            ) ** 0.5  # 全体速度
    except Exception as e:
        print(f"Speed error: {e}")
        speed = 0

    try:
        wifi_snr = tello.get_wifi()  # Wi-Fi SNR
    except Exception as e:
        print(f"WiFi error: {e}")
        wifi_snr = 0

    #描画
    screen.blit(frame_surface, (0, 0))

    # ===== HUD =====

    # バッテリー
    battery_text = font.render(
        f"BAT: {battery}%",
        True,
        (0,255,0)
    )

    screen.blit(
        battery_text,
        (
            width - battery_text.get_width() - 10,
            10
        )
    )

    # 高度
    height_text = small_font.render(
        f"ALT: {height}cm",
        True,
        (255,255,255)
    )

    screen.blit(
        height_text,
        (10,10)
    )

    # 速度
    speed_text = small_font.render(
        f"SPD: {speed:.1f}cm/s",
        True,
        (255,255,255)
    )

    screen.blit(
        speed_text,
        (10,40)
    )

    # WiFi
    if wifi_snr > -60:
        wifi_color = (0,255,0)
    elif wifi_snr > -80:
        wifi_color = (255,255,0)
    else:
        wifi_color = (255,0,0)

    wifi_text = small_font.render(
        f"WIFI: {wifi_snr}dB",
        True,
        wifi_color
    )

    screen.blit(
        wifi_text,
        (10,70)
    )

    # 飛行状態
    status = (
        "FLYING"
        if is_flying
        else "LANDED"
    )

    status_color = (
        (0,255,0)
        if is_flying
        else (255,0,0)
    )

    status_text = font.render(
        status,
        True,
        status_color
    )

    screen.blit(
        status_text,
        (
            width // 2
            - status_text.get_width() // 2,
            height - 50
        )
    )

    # ===== CONTROL HUD =====

    title = small_font.render(
        "FLIGHT CONTROL",
        True,
        (0,255,0)
    )

    screen.blit(
        title,
        (
            width - 190,
            height - 145
        )
    )

    controls = [
        (
            "W/S  FWD/BACK",
            keys[pygame.K_w]
            or keys[pygame.K_s]
        ),

        (
            "A/D  YAW",
            keys[pygame.K_a]
            or keys[pygame.K_d]
        ),

        (
            "UP/DN ALT",
            keys[pygame.K_UP]
            or keys[pygame.K_DOWN]
        ),

        (
            "LT/RT STRAFE",
            keys[pygame.K_LEFT]
            or keys[pygame.K_RIGHT]
        ),

        (
            "SPC TAKEOFF",
            False
        ),

        (
            "BSP EMERG",
            False
        )
    ]

    for i, (text, active) in enumerate(controls):

        color = (
            (0,255,0)
            if active
            else (120,220,120)
        )

        surf = small_font.render(
            text,
            True,
            color
        )

        screen.blit(
            surf,
            (
                width - 190,
                height - 120 + i * 18
            )
        )

    pygame.display.update()

    clock.tick(30)

#終了
if is_flying:
    try:
        tello.land()
    except:
        pass

tello.streamoff()
tello.end()
pygame.quit()
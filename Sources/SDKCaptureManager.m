#import "SDKCaptureManager.h"
#import "SDKAssetExport.h"
#import <QuartzCore/QuartzCore.h>
#import <dispatch/dispatch.h>
#import <mach-o/dyld.h>
#import <mach-o/loader.h>
#import <objc/runtime.h>
#import <math.h>
#import <stdlib.h>
#import <string.h>

static const NSTimeInterval SDKCaptureDuration = 90.0;
static const NSUInteger SDKMaxEvents = 2000;
static const NSUInteger SDKMaxSnapshots = 184;
static const NSUInteger SDKMaxMarkers = 64;
static const NSUInteger SDKMaxReportBytes = 12 * 1024 * 1024;
static void (*SDKPreviousSendEvent)(id, SEL, UIEvent *) = NULL;
static BOOL SDKObserverInstalled = NO;

static double SDKMonotonicTime(void) { return NSProcessInfo.processInfo.systemUptime; }
static NSString *SDKClassName(id object) {
    NSString *name = object ? NSStringFromClass([object class]) : @"none";
    return name.length > 96 ? [name substringToIndex:96] : name;
}
static BOOL SDKNonemptyString(id value, NSUInteger maximum) {
    return [value isKindOfClass:NSString.class] && [value length] > 0 && [value length] <= maximum;
}
static NSArray<NSNumber *> *SDKRectValues(CGRect rectangle) {
    if (!isfinite(rectangle.origin.x) || !isfinite(rectangle.origin.y) ||
        !isfinite(rectangle.size.width) || !isfinite(rectangle.size.height)) return @[];
    return @[@(rectangle.origin.x), @(rectangle.origin.y), @(rectangle.size.width), @(rectangle.size.height)];
}

/// Read only loaded Mach-O metadata. No memory scanning or native function calls.
static NSString *SDKImageUUID(const struct mach_header *raw) {
    if (!raw || raw->magic != MH_MAGIC_64 || raw->cputype != CPU_TYPE_ARM64) return nil;
    const struct mach_header_64 *header = (const struct mach_header_64 *)raw;
    if (header->ncmds > 4096 || header->sizeofcmds > 1024 * 1024) return nil;
    const uint8_t *commands = (const uint8_t *)(header + 1);
    NSUInteger position = 0;
    for (uint32_t index = 0; index < header->ncmds; ++index) {
        if (position > header->sizeofcmds || header->sizeofcmds - position < sizeof(struct load_command)) return nil;
        const struct load_command *command = (const struct load_command *)(commands + position);
        if (command->cmdsize < sizeof(*command) || command->cmdsize > header->sizeofcmds - position) return nil;
        if (command->cmd == LC_UUID && command->cmdsize == sizeof(struct uuid_command)) {
            const struct uuid_command *uuidCommand = (const struct uuid_command *)command;
            return [[[NSUUID alloc] initWithUUIDBytes:uuidCommand->uuid] UUIDString].lowercaseString;
        }
        position += command->cmdsize;
    }
    return nil;
}

static BOOL SDKAcceptWindow(UIWindow *window) {
    NSString *name = SDKClassName(window);
    return !window.hidden && window.alpha > 0 &&
        ![name hasPrefix:@"SDK"] && ![name containsString:@"Keyboard"] &&
        ![name containsString:@"TextEffects"] && ![name containsString:@"Remote"] &&
        ![name containsString:@"Auth"];
}
static NSInteger SDKWindowOrientation(UIWindow *window) {
    return window.windowScene ? window.windowScene.interfaceOrientation : UIApplication.sharedApplication.statusBarOrientation;
}
static NSArray<UIWindow *> *SDKGameWindows(void) {
    NSMutableArray<UIWindow *> *windows = [NSMutableArray array];
    UIApplication *application = UIApplication.sharedApplication;
    for (UIScene *scene in application.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class] || scene.activationState != UISceneActivationStateForegroundActive) continue;
        for (UIWindow *window in ((UIWindowScene *)scene).windows) {
            if (!SDKAcceptWindow(window)) continue;
            [windows addObject:window];
            if (windows.count >= 8) return windows;
        }
    }
    // Apps without UIApplicationSceneManifest still use UIApplication.windows.
    if (windows.count == 0 && application.applicationState == UIApplicationStateActive) {
        for (UIWindow *window in application.windows) {
            if (window.windowScene || !SDKAcceptWindow(window)) continue;
            [windows addObject:window];
            if (windows.count >= 8) break;
        }
    }
    return windows;
}
static BOOL SDKExcludedView(UIView *view) {
    for (NSUInteger depth = 0; view && depth < 24; ++depth, view = view.superview) {
        NSString *name = SDKClassName(view);
        if ([view conformsToProtocol:@protocol(UITextInput)] ||
            [view isKindOfClass:UITextField.class] || [view isKindOfClass:UITextView.class] ||
            [name hasPrefix:@"SDK"] || [name containsString:@"Keyboard"] ||
            [name containsString:@"TextEffects"]) return YES;
    }
    return NO;
}

@interface SDKCaptureManager ()
@property(nonatomic, readwrite) BOOL recording;
@property(nonatomic, copy, readwrite) NSString *statusText;
@property(nonatomic, strong) NSDictionary *identity;
@property(nonatomic, strong) NSMutableArray *events;
@property(nonatomic, strong) NSMutableArray *snapshots;
@property(nonatomic, strong) NSMutableArray *markers;
@property(nonatomic, strong) NSArray *imageInventory;
@property(nonatomic, strong) NSArray *classInventory;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) NSMapTable<UITouch *, NSNumber *> *touchIDs;
@property(nonatomic, strong) NSMapTable<UITouch *, NSNumber *> *touchTimes;
@property(nonatomic, strong) NSMapTable<UIView *, NSNumber *> *viewIDs;
@property(nonatomic, copy) NSString *sessionID;
@property(nonatomic, copy) NSString *startedUTC;
@property(nonatomic, copy) NSString *stopReason;
@property(nonatomic) double startedAt;
@property(nonatomic) double endedAt;
@property(nonatomic) double lastManualSnapshot;
@property(nonatomic) NSUInteger nextTouchID;
@property(nonatomic) NSUInteger nextViewID;
@property(nonatomic) NSUInteger droppedEvents;
@property(nonatomic) BOOL exporting;
- (void)observeEvent:(UIEvent *)event;
- (void)stopWithReason:(NSString *)reason;
@end

static void SDKObservedSendEvent(id application, SEL selector, UIEvent *event) {
    // Only our observation is guarded. Never swallow an exception from the app,
    // repeat original delivery, retain UIEvent/UITouch, or mutate the event.
    @try { [[SDKCaptureManager shared] observeEvent:event]; }
    @catch (__unused NSException *exception) { /* Deliberately omit exception text. */ }
    SDKPreviousSendEvent(application, selector, event);
}
static BOOL SDKInstallEventObserver(void) {
    if (SDKObserverInstalled) return YES;
    if (!NSThread.isMainThread) return NO;
    Class applicationClass = UIApplication.sharedApplication.class;
    SEL selector = @selector(sendEvent:);
    Method method = class_getInstanceMethod(applicationClass, selector);
    if (!method || method_getNumberOfArguments(method) != 3) return NO;
    char *returnType = method_copyReturnType(method);
    char *eventType = method_copyArgumentType(method, 2);
    BOOL compatible = returnType && !strcmp(returnType, "v") && eventType && eventType[0] == '@';
    free(returnType); free(eventType);
    if (!compatible) return NO;
    IMP previous = method_getImplementation(method);
    if (!previous || previous == (IMP)SDKObservedSendEvent) return NO;
    SDKPreviousSendEvent = (void (*)(id, SEL, UIEvent *))previous;
    // Add an override when sendEvent: is inherited, otherwise replace the
    // concrete implementation. The inherited superclass remains untouched.
    if (!class_addMethod(applicationClass, selector, (IMP)SDKObservedSendEvent, method_getTypeEncoding(method)))
        class_replaceMethod(applicationClass, selector, (IMP)SDKObservedSendEvent, method_getTypeEncoding(method));
    SDKObserverInstalled = YES;
    return YES;
}

@implementation SDKCaptureManager
+ (instancetype)shared {
    static SDKCaptureManager *instance;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ instance = [[self alloc] init]; });
    return instance;
}
- (instancetype)init {
    if ((self = [super init])) {
        _statusText = @"Pronto para capturar";
        NSNotificationCenter *center = NSNotificationCenter.defaultCenter;
        [center addObserver:self selector:@selector(applicationInactive:) name:UIApplicationWillResignActiveNotification object:nil];
        [center addObserver:self selector:@selector(keyboardShown:) name:UIKeyboardWillShowNotification object:nil];
    }
    return self;
}
- (BOOL)validateTarget {
    NSBundle *framework = [NSBundle bundleForClass:self.class];
    NSURL *url = [framework URLForResource:@"Target" withExtension:@"json"];
    NSNumber *fileSize = nil;
    if (!url || ![url getResourceValue:&fileSize forKey:NSURLFileSizeKey error:nil] ||
        fileSize.unsignedLongLongValue > 64 * 1024) {
        self.statusText = @"Configuração da base ausente"; return NO;
    }
    NSData *data = [NSData dataWithContentsOfURL:url options:NSDataReadingMappedIfSafe error:nil];
    if (!data || data.length > 64 * 1024) { self.statusText = @"Configuração inválida"; return NO; }
    id document = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![document isKindOfClass:NSDictionary.class] || ![document[@"schema_version"] isEqual:@1] ||
        ![document[@"target"] isKindOfClass:NSDictionary.class]) {
        self.statusText = @"Formato da configuração incompatível"; return NO;
    }
    NSDictionary *target = document[@"target"];
    for (NSString *key in @[@"bundle_identifier", @"version", @"build", @"executable", @"main_uuid", @"main_sha256"])
        if (!SDKNonemptyString(target[key], 256)) { self.statusText = @"Identidade da base incompleta"; return NO; }
    NSBundle *main = NSBundle.mainBundle;
    NSString *version = [main objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    NSString *build = [main objectForInfoDictionaryKey:@"CFBundleVersion"];
    NSString *executable = [main objectForInfoDictionaryKey:@"CFBundleExecutable"];
    NSString *uuid = SDKImageUUID(_dyld_get_image_header(0));
    if (![main.bundleIdentifier isEqualToString:target[@"bundle_identifier"]] ||
        ![version isEqualToString:target[@"version"]] || ![build isEqualToString:target[@"build"]] ||
        ![executable isEqualToString:target[@"executable"]] ||
        !uuid || ![uuid isEqualToString:[target[@"main_uuid"] lowercaseString]]) {
        self.statusText = @"Base diferente; captura bloqueada"; return NO;
    }
    self.identity = @{@"bundle_identifier": main.bundleIdentifier, @"version": version, @"build": build,
        @"executable": executable, @"main_uuid": uuid, @"architecture": @"arm64", @"module_version": @"0.1.0",
        @"base_main_sha256": target[@"main_sha256"], @"base_hash_scope": @"original_executable_before_packaging_and_signing",
        @"ios_version": UIDevice.currentDevice.systemVersion};
    return YES;
}
- (NSArray *)loadedImages {
    NSMutableArray *rows = [NSMutableArray array];
    NSString *prefix = [NSBundle.mainBundle.bundlePath stringByAppendingString:@"/"];
    uint32_t count = MIN(_dyld_image_count(), 2048u);
    for (uint32_t index = 0; index < count && rows.count < 128; ++index) {
        const char *name = _dyld_get_image_name(index);
        if (!name) continue;
        NSString *path = [NSString stringWithUTF8String:name];
        if (!path || ![path hasPrefix:prefix]) continue;
        NSString *relative = [path substringFromIndex:prefix.length];
        if (relative.length > 256) continue;
        [rows addObject:@{@"bundle_relative_image": relative,
            @"uuid": SDKImageUUID(_dyld_get_image_header(index)) ?: @"unavailable"}];
    }
    return rows;
}
- (NSArray *)mainImageClasses {
    const char *name = _dyld_get_image_name(0);
    if (!name) return @[];
    unsigned int count = 0;
    const char **names = objc_copyClassNamesForImage(name, &count);
    NSMutableArray *rows = [NSMutableArray array];
    for (unsigned int index = 0; names && index < count && index < 512; ++index) {
        NSString *className = [NSString stringWithUTF8String:names[index]];
        if (className && className.length <= 192) [rows addObject:className];
    }
    free(names);
    return [rows sortedArrayUsingSelector:@selector(compare:)];
}
- (NSNumber *)identifierForView:(UIView *)view {
    if (!view) return @0;
    NSNumber *identifier = [self.viewIDs objectForKey:view];
    if (!identifier) {
        identifier = @(++self.nextViewID);
        [self.viewIDs setObject:identifier forKey:view];
    }
    return identifier;
}
- (void)startCapture {
    if (!NSThread.isMainThread || self.recording || self.exporting) return;
    if (UIApplication.sharedApplication.applicationState != UIApplicationStateActive) {
        self.statusText = @"Volte ao jogo para iniciar"; return;
    }
    if (![self validateTarget]) return;
    if (!SDKInstallEventObserver()) { self.statusText = @"Observador de toques incompatível"; return; }
    self.events = [NSMutableArray array]; self.snapshots = [NSMutableArray array]; self.markers = [NSMutableArray array];
    self.touchIDs = [NSMapTable weakToStrongObjectsMapTable]; self.touchTimes = [NSMapTable weakToStrongObjectsMapTable];
    self.viewIDs = [NSMapTable weakToStrongObjectsMapTable];
    self.nextTouchID = 0; self.nextViewID = 0; self.droppedEvents = 0;
    self.sessionID = NSUUID.UUID.UUIDString;
    self.startedUTC = [[[NSISO8601DateFormatter alloc] init] stringFromDate:NSDate.date];
    self.imageInventory = [self loadedImages]; self.classInventory = [self mainImageClasses];
    self.stopReason = @"recording"; self.startedAt = SDKMonotonicTime(); self.endedAt = 0;
    self.lastManualSnapshot = -1; self.recording = YES;
    [self captureSnapshotWithReason:@"start"];
    self.statusText = @"Capturando 90 s; jogue manualmente";
    self.timer = [NSTimer timerWithTimeInterval:0.5 target:self selector:@selector(captureTick:) userInfo:nil repeats:YES];
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
}
- (void)captureTick:(NSTimer *)timer {
    if (!self.recording) return;
    double elapsed = SDKMonotonicTime() - self.startedAt;
    if (elapsed >= SDKCaptureDuration) { [self stopWithReason:@"duration_limit"]; return; }
    [self captureSnapshotWithReason:@"timer"];
    self.statusText = [NSString stringWithFormat:@"%.0f/90 s · %lu toques · %lu leituras", elapsed,
        (unsigned long)self.events.count, (unsigned long)self.snapshots.count];
}
- (void)appendView:(UIView *)view window:(UIWindow *)window depth:(NSUInteger)depth rows:(NSMutableArray *)rows limit:(NSUInteger)limit {
    if (!view || depth > 8 || rows.count >= limit || view.hidden || SDKExcludedView(view)) return;
    CGRect rectangle = [view convertRect:view.bounds toView:window];
    NSArray *bounds = SDKRectValues(rectangle);
    if (bounds.count != 4 || !isfinite(view.alpha)) return;
    [rows addObject:@{@"view_id": [self identifierForView:view], @"parent_id": [self identifierForView:view.superview],
        @"class": SDKClassName(view), @"layer_class": SDKClassName(view.layer), @"depth": @(depth),
        @"bounds_points": bounds, @"interactive": @(view.userInteractionEnabled), @"alpha": @(view.alpha)}];
    for (UIView *child in view.subviews) {
        [self appendView:child window:window depth:depth + 1 rows:rows limit:limit];
        if (rows.count >= limit) break;
    }
}
- (void)captureSnapshotWithReason:(NSString *)reason {
    if (self.snapshots.count >= SDKMaxSnapshots) return;
    NSMutableArray *windows = [NSMutableArray array];
    NSUInteger remaining = 96;
    for (UIWindow *window in SDKGameWindows()) {
        NSMutableArray *views = [NSMutableArray array];
        NSUInteger limit = remaining;
        [self appendView:window window:window depth:0 rows:views limit:limit];
        remaining -= views.count;
        UIViewController *controller = window.rootViewController;
        for (NSUInteger count = 0; controller.presentedViewController && count < 8; ++count) controller = controller.presentedViewController;
        [windows addObject:@{@"window_id": [self identifierForView:window], @"window_class": SDKClassName(window),
            @"controller_class": SDKClassName(controller), @"bounds_points": SDKRectValues(window.bounds),
            @"scale": @(window.screen.scale), @"orientation": @(SDKWindowOrientation(window)),
            @"orientation_source": window.windowScene ? @"window_scene" : @"legacy_status_bar",
            @"views": views, @"view_limit_reached": @(views.count >= limit)}];
    }
    [self.snapshots addObject:@{@"elapsed_ms": @((SDKMonotonicTime() - self.startedAt) * 1000.0), @"reason": reason,
        @"windows": windows, @"game_state": @{@"status": @"native_state_not_mapped",
            @"players": NSNull.null, @"entities": NSNull.null, @"projectiles": NSNull.null,
            @"health": NSNull.null, @"ammo": NSNull.null}}];
}
- (void)captureManualSnapshot {
    if (!NSThread.isMainThread || !self.recording) return;
    double now = SDKMonotonicTime();
    if (now - self.startedAt >= SDKCaptureDuration) { [self stopWithReason:@"duration_limit"]; return; }
    if (self.lastManualSnapshot >= 0 && now - self.lastManualSnapshot < 0.5) return;
    self.lastManualSnapshot = now;
    [self captureSnapshotWithReason:@"manual"];
    self.statusText = @"Leitura manual registrada";
}
- (void)recordMarker {
    if (!NSThread.isMainThread || !self.recording || self.markers.count >= SDKMaxMarkers) return;
    double now = SDKMonotonicTime();
    if (now - self.startedAt >= SDKCaptureDuration) { [self stopWithReason:@"duration_limit"]; return; }
    [self.markers addObject:@{@"elapsed_ms": @((now - self.startedAt) * 1000.0), @"label": @"manual_play"}];
    self.statusText = @"Marcador de jogada registrado";
}
- (void)observeEvent:(UIEvent *)event {
    if (!NSThread.isMainThread || !self.recording || event.type != UIEventTypeTouches) return;
    double now = SDKMonotonicTime();
    if (now - self.startedAt >= SDKCaptureDuration) { [self stopWithReason:@"duration_limit"]; return; }
    NSArray<UIWindow *> *acceptedWindows = SDKGameWindows();
    for (UITouch *touch in event.allTouches) {
        if (self.events.count >= SDKMaxEvents) { self.droppedEvents += 1; [self stopWithReason:@"event_limit"]; break; }
        UIWindow *window = touch.window;
        UIView *view = touch.view;
        if (!window || !view || ![acceptedWindows containsObject:window] || SDKExcludedView(view)) continue;
        if (touch.phase == UITouchPhaseStationary) continue;
        NSNumber *lastTime = [self.touchTimes objectForKey:touch];
        if (touch.phase == UITouchPhaseMoved && lastTime && now - lastTime.doubleValue < 0.10) continue;
        NSNumber *identifier = [self.touchIDs objectForKey:touch];
        if (!identifier) {
            if (self.touchIDs.count >= 32) { self.droppedEvents += 1; continue; }
            identifier = @(++self.nextTouchID); [self.touchIDs setObject:identifier forKey:touch];
        }
        CGPoint point = [touch locationInView:window];
        CGRect bounds = window.bounds;
        if (!isfinite(point.x) || !isfinite(point.y) || SDKRectValues(bounds).count != 4 ||
            bounds.size.width <= 0 || bounds.size.height <= 0) continue;
        NSArray *phases = @[@"began", @"moved", @"stationary", @"ended", @"cancelled"];
        NSInteger phase = touch.phase;
        if (phase < 0 || phase >= (NSInteger)phases.count) continue;
        [self.events addObject:@{@"elapsed_ms": @((now - self.startedAt) * 1000.0), @"touch_id": identifier,
            @"phase": phases[(NSUInteger)phase], @"view_id": [self identifierForView:view],
            @"window_id": [self identifierForView:window], @"view_class": SDKClassName(view), @"window_class": SDKClassName(window),
            @"point": @[@(point.x), @(point.y)],
            @"normalized": @[@((point.x - bounds.origin.x) / bounds.size.width), @((point.y - bounds.origin.y) / bounds.size.height)],
            @"window_bounds_points": SDKRectValues(bounds), @"scale": @(window.screen.scale),
            @"orientation": @(SDKWindowOrientation(window)),
            @"orientation_source": window.windowScene ? @"window_scene" : @"legacy_status_bar",
            @"capture_order": @"before_original_sendEvent"}];
        [self.touchTimes setObject:@(now) forKey:touch];
        if (touch.phase == UITouchPhaseEnded || touch.phase == UITouchPhaseCancelled) {
            [self.touchIDs removeObjectForKey:touch]; [self.touchTimes removeObjectForKey:touch];
        }
    }
}
- (void)stopCapture { [self stopWithReason:@"manual_stop"]; }
- (void)stopWithReason:(NSString *)reason {
    if (!NSThread.isMainThread || !self.recording) return;
    self.recording = NO; self.stopReason = reason;
    [self.timer invalidate]; self.timer = nil;
    [self captureSnapshotWithReason:@"stop"];
    // Include the final snapshot's actual acquisition time in the session span.
    self.endedAt = SDKMonotonicTime();
    [self.touchIDs removeAllObjects]; [self.touchTimes removeAllObjects];
    self.statusText = [NSString stringWithFormat:@"Pronto: %lu eventos. Exporte o JSON", (unsigned long)self.events.count];
}
- (void)applicationInactive:(NSNotification *)notification { [self stopWithReason:@"application_inactive"]; }
- (void)keyboardShown:(NSNotification *)notification { [self stopWithReason:@"keyboard_visible"]; }
- (void)exportCaptureFromViewController:(UIViewController *)presenter sourceView:(UIView *)sourceView {
    if (!NSThread.isMainThread || self.exporting) return;
    [self stopWithReason:@"export_requested"];
    if (!self.sessionID || !self.identity) { self.statusText = @"Faça uma captura antes de exportar"; return; }
    if (!presenter.view.window || presenter.presentedViewController) {
        self.statusText = @"Feche a janela atual antes de exportar"; return;
    }
    self.exporting = YES;
    NSDictionary *report = @{@"schema": @"SDKLaserCapture.Diagnostic", @"schema_version": @1,
        @"identity": self.identity, @"session_id": self.sessionID, @"start_utc": self.startedUTC,
        @"duration_ms": @((self.endedAt - self.startedAt) * 1000.0), @"stop_reason": self.stopReason,
        @"events": [self.events copy], @"snapshots": [self.snapshots copy], @"markers": [self.markers copy],
        @"images": self.imageInventory ?: @[], @"main_image_objc_class_names": self.classInventory ?: @[],
        @"limits": @{@"duration_seconds": @90, @"max_events": @(SDKMaxEvents), @"max_snapshots": @(SDKMaxSnapshots),
            @"max_views_per_snapshot": @96, @"max_markers": @(SDKMaxMarkers), @"max_report_bytes": @(SDKMaxReportBytes),
            @"dropped_events": @(self.droppedEvents)},
        @"capabilities": @{@"ui_geometry": @YES, @"touch_timing": @YES, @"image_metadata": @YES,
            @"native_game_state_reader": @NO, @"native_function_tracing": @NO, @"code_extraction": @NO,
            @"autoplay": @NO, @"source_code_recovered": @NO, @"screenshots": @NO, @"network_capture": @NO},
        @"privacy": @{@"text_field_content": @NO, @"credentials": @NO, @"clipboard": @NO,
            @"heap_dump": @NO, @"automatic_upload": @NO, @"input_views_excluded": @YES}};
    NSString *session = [self.sessionID copy];
    __weak SDKCaptureManager *weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSError *error = nil;
        NSFileManager *manager = NSFileManager.defaultManager;
        NSURL *cache = [manager URLsForDirectory:NSCachesDirectory inDomains:NSUserDomainMask].firstObject;
        NSURL *root = [cache URLByAppendingPathComponent:@"SDKLaserCaptureExports" isDirectory:YES];
        NSURL *folder = [root URLByAppendingPathComponent:[@"capture-" stringByAppendingString:session] isDirectory:YES];
        NSURL *output = nil;
        NSMutableArray<NSURL *> *items = [NSMutableArray array];
        BOOL overBudget = NO;
        NSUInteger assetCount = 0;
        if (folder && [manager createDirectoryAtURL:folder withIntermediateDirectories:YES attributes:nil error:&error]) {
            NSDictionary *assets = [SDKAssetExport exportTablesToDirectory:folder];
            NSMutableDictionary *completeReport = [report mutableCopy];
            completeReport[@"static_assets_exported"] = assets[@"records"] ?: @[];
            completeReport[@"static_asset_export_status"] = assets[@"status"] ?: @"unavailable";
            completeReport[@"static_assets_scope"] = @"verified_bundle_tables_not_live_match_state";
            NSData *data = [NSJSONSerialization dataWithJSONObject:completeReport options:NSJSONWritingSortedKeys error:&error];
            overBudget = data.length > SDKMaxReportBytes;
            if (data && !overBudget) {
                output = [folder URLByAppendingPathComponent:[NSString stringWithFormat:@"capture-%@.json", session]];
                if (![data writeToURL:output options:NSDataWritingAtomic | NSDataWritingFileProtectionComplete error:&error]) output = nil;
            }
            if (output) {
                [items addObject:output];
                NSArray<NSURL *> *tables = assets[@"files"] ?: @[];
                [items addObjectsFromArray:tables]; assetCount = tables.count;
                NSArray<NSURL *> *old = [manager contentsOfDirectoryAtURL:root includingPropertiesForKeys:nil options:0 error:nil];
                for (NSURL *candidate in old) {
                    if ([candidate isEqual:folder]) continue;
                    if ([candidate.lastPathComponent hasPrefix:@"capture-"]) [manager removeItemAtURL:candidate error:nil];
                }
            } else {
                // A failed export never publishes a partial bundle for sharing.
                [manager removeItemAtURL:folder error:nil];
            }
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            SDKCaptureManager *strongSelf = weakSelf;
            strongSelf.exporting = NO;
            if (!output) {
                strongSelf.statusText = overBudget ? @"JSON excedeu 12 MiB; faça uma captura menor" : @"Falha ao salvar o JSON";
                return;
            }
            if (!presenter.view.window || presenter.presentedViewController ||
                UIApplication.sharedApplication.applicationState != UIApplicationStateActive) {
                strongSelf.statusText = @"Volte ao jogo e toque em Exportar novamente"; return;
            }
            UIActivityViewController *activity = [[UIActivityViewController alloc] initWithActivityItems:[items copy] applicationActivities:nil];
            activity.popoverPresentationController.sourceView = sourceView ?: presenter.view;
            activity.popoverPresentationController.sourceRect = (sourceView ?: presenter.view).bounds;
            activity.excludedActivityTypes = @[UIActivityTypePostToFacebook, UIActivityTypePostToTwitter,
                UIActivityTypeAssignToContact, UIActivityTypeSaveToCameraRoll];
            [presenter presentViewController:activity animated:YES completion:nil];
            strongSelf.statusText = [NSString stringWithFormat:@"JSON + %lu/5 CSV. Escolha onde salvar", (unsigned long)assetCount];
        });
    });
}
@end

package com.readabilityrss.reader;

import android.os.Bundle;

import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import com.getcapacitor.BridgeActivity;

import java.util.concurrent.TimeUnit;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .setRequiresBatteryNotLow(true)
                .build();

        PeriodicWorkRequest syncWork = new PeriodicWorkRequest.Builder(
                ArticleSyncWorker.class, 6, TimeUnit.HOURS)
                .setConstraints(constraints)
                .build();

        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
                "article_sync",
                ExistingPeriodicWorkPolicy.KEEP,
                syncWork
        );
    }
}
